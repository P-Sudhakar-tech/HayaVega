"""Benchmark the bigger swing from the planned template-performance work:
compile a parsed template into a real Python function via compile()/exec()
on the trusted, already-parsed node tree -- matching what Jinja2/Mako
actually do, and the only lever structurally capable of matching Jinja2's
speed rather than shaving a few percent (see the quick win in
template_path_fix.py for that smaller, already-shipped improvement).

Scope, deliberately bounded for safety and correctness:
- Node-level structure (text/if/elif/else/for) is compiled to REAL Python
  control flow (actual `if`/`elif`/`else`/`for` statements) -- this removes
  the per-render isinstance() dispatch _render_nodes does on every node,
  which is the more mechanical, lower-risk part of "compile to bytecode."
- Expression evaluation (_Path/_Literal/_Not/_Compare/_BoolOp) is NOT
  compiled to raw Python operators here -- the generated code still calls
  the existing, tested _evaluate() interpreter for every {{ }}/condition.
  This is a deliberate scope boundary: _evaluate()'s undefined-vs-None
  semantics, the `in` operator's TypeError->False fallback, and Python
  truthiness edge cases are exactly the kind of subtle behavior that's
  easy to get wrong reimplementing as raw generated operators, and this
  benchmark exists to test whether compiling the OUTER structure alone is
  worth it before ever taking on that additional risk.
- `{% extends %}`/`{% block %}` are not supported (same boundary the
  earlier native_template_engine.py candidate drew) -- a template using
  them raises `_Unsupported`.

Safety note on code generation: template TEXT content is embedded via
Python's own `repr()`, which safely escapes any quotes/backslashes/control
characters in the string -- never via naive string formatting/interpolation
into source text. Expression AST objects (_Path etc.) are never
stringified into source at all: they live in a plain Python list built at
compile time and are referenced by index (`_E[3]`) in the generated code,
sidestepping any need to serialize them as source syntax.

Requires nothing beyond the standard library.

Usage:
    python benchmarks/compiled_template_engine.py
"""

from __future__ import annotations

import sys
import timeit
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hayavega.templates import _UNDEFINED, _BlockNode, _ForNode, _IfNode, _resolve_chain, _TextNode, _VariableNode, render_template
from hayavega.templates import _evaluate as evaluate
from hayavega.templates import _truthy as truthy


class _Unsupported(Exception):
    """Raised when a template uses a construct this candidate doesn't compile (extends/block)."""


class _CompiledTemplateFunction:
    """One compiled template: a real Python function built via compile()/exec()
    from the parsed node tree, cached the same way the interpreter caches its
    parsed nodes -- compile once per file, call the function on every render."""

    def __init__(self, nodes: list, template_name: str, debug: bool) -> None:
        self._expr_pool: list = []
        lines = ["def _render(context):", "    _out = []"]
        lines.extend(self._compile_nodes(nodes, indent=1))
        lines.append("    return ''.join(_out)")
        source = "\n".join(lines)
        namespace = {
            "_evaluate": evaluate,
            "_truthy": truthy,
            "_UNDEFINED": _UNDEFINED,
            "_E": self._expr_pool,
            "_NAME": template_name,
            "_DEBUG": debug,
            "escape": __import__("html").escape,
        }
        code = compile(source, f"<compiled template {template_name!r}>", "exec")
        exec(code, namespace)  # noqa: S102 -- `source` is generated entirely by this class from a trusted, already-parsed node tree, never from caller-supplied text; see module docstring.
        self._render = namespace["_render"]
        self.source = source  # kept for inspection/debugging, not used at render time

    def render(self, context: dict) -> str:
        return self._render(context)

    def _add_expr(self, expr) -> int:
        self._expr_pool.append(expr)
        return len(self._expr_pool) - 1

    def _compile_nodes(self, nodes: list, indent: int) -> list[str]:
        pad = "    " * indent
        lines: list[str] = []
        for node in nodes:
            if isinstance(node, _TextNode):
                lines.append(f"{pad}_out.append({node.text!r})")
            elif isinstance(node, _VariableNode):
                index = self._add_expr(node.expr)
                lines.append(f"{pad}_v = _evaluate(_E[{index}], context, _NAME, {node.line}, _DEBUG)")
                lines.append(f"{pad}if _v is not _UNDEFINED:")
                lines.append(f"{pad}    _out.append(escape(str(_v)))")
            elif isinstance(node, _IfNode):
                lines.extend(self._compile_if(node, indent))
            elif isinstance(node, _ForNode):
                lines.extend(self._compile_for(node, indent))
            elif isinstance(node, _BlockNode):
                raise _Unsupported("{% block %}/{% extends %} are not supported by the compiled template engine")
            else:
                raise _Unsupported(f"unknown node: {node!r}")
        return lines

    def _compile_if(self, node: _IfNode, indent: int) -> list[str]:
        pad = "    " * indent
        lines: list[str] = []
        for position, (condition, body, line) in enumerate(node.branches):
            body_lines = self._compile_nodes(body, indent + 1) or [f"{pad}    pass"]
            if condition is None:
                lines.append(f"{pad}else:")
            else:
                index = self._add_expr(condition)
                keyword = "if" if position == 0 else "elif"
                lines.append(f"{pad}{keyword} _truthy(_evaluate(_E[{index}], context, _NAME, {line}, _DEBUG)):")
            lines.extend(body_lines)
        return lines

    def _compile_for(self, node: _ForNode, indent: int) -> list[str]:
        pad = "    " * indent
        var = node.var_name
        index = self._add_expr(node.iterable)
        lines = [
            f"{pad}_iterable = _evaluate(_E[{index}], context, _NAME, {node.line}, _DEBUG)",
            f"{pad}_items = [] if _iterable is _UNDEFINED or _iterable in (None, '') else list(_iterable)",
            f"{pad}_count = len(_items)",
            f"{pad}_had_var = {var!r} in context",
            f"{pad}_prev_var = context.get({var!r})",
            f"{pad}_had_loop = 'loop' in context",
            f"{pad}_prev_loop = context.get('loop')",
            f"{pad}for _index, _item in enumerate(_items):",
            f"{pad}    context[{var!r}] = _item",
            f"{pad}    context['loop'] = {{'index': _index + 1, 'index0': _index, 'first': _index == 0, 'last': _index + 1 == _count}}",
        ]
        lines.extend(self._compile_nodes(node.body, indent + 1) or [f"{pad}    pass"])
        lines.append(f"{pad}if _had_var:")
        lines.append(f"{pad}    context[{var!r}] = _prev_var")
        lines.append(f"{pad}else:")
        lines.append(f"{pad}    context.pop({var!r}, None)")
        lines.append(f"{pad}if _had_loop:")
        lines.append(f"{pad}    context['loop'] = _prev_loop")
        lines.append(f"{pad}else:")
        lines.append(f"{pad}    context.pop('loop', None)")
        return lines


_compiled_cache: dict[str, _CompiledTemplateFunction] = {}


def compile_template(template_name: str, directories: tuple[Path, ...]) -> _CompiledTemplateFunction:
    cached = _compiled_cache.get(template_name)
    if cached is not None:
        return cached
    root, _blocks = _resolve_chain(template_name, directories)
    if root.extends is not None:
        raise _Unsupported("{% extends %} is not supported by the compiled template engine")
    compiled = _CompiledTemplateFunction(root.body, template_name, debug=False)
    _compiled_cache[template_name] = compiled
    return compiled


# --- Benchmark 1: the real benchmark suite's page.html (variables + for) ---

_TEMPLATE_DIR = Path(__file__).resolve().parents[0] / "hayavega_app" / "templates"
_POSTS_CONTEXT = {"posts": [{"title": f"Post {i}", "views": i * 3} for i in range(20)]}
_PAGE_COMPILED = compile_template("page.html", (_TEMPLATE_DIR,))


def _python_page() -> str:
    return render_template("page.html", _POSTS_CONTEXT, directories=(_TEMPLATE_DIR,))


def _compiled_page() -> str:
    return _PAGE_COMPILED.render(dict(_POSTS_CONTEXT))


# --- Benchmark 2: a synthetic template exercising if/elif/else more heavily -

_IF_HEAVY_SOURCE = """<ul>
{% for post in posts %}
{% if post.views == 0 %}
<li class="new">{{ post.title }}</li>
{% elif post.featured %}
<li class="featured">{{ post.title }} ({{ post.views }})</li>
{% else %}
<li>{{ post.title }} ({{ post.views }})</li>
{% endif %}
{% endfor %}
</ul>
"""

_IF_HEAVY_DIR = Path(__file__).resolve().parent / "compiled_template_engine_fixtures"
_IF_HEAVY_DIR.mkdir(exist_ok=True)
(_IF_HEAVY_DIR / "if_heavy.html").write_text(_IF_HEAVY_SOURCE, encoding="utf-8")
_IF_HEAVY_CONTEXT = {"posts": [{"title": f"Post {i}", "views": i * 3, "featured": i % 5 == 0} for i in range(20)]}
_IF_HEAVY_COMPILED = compile_template("if_heavy.html", (_IF_HEAVY_DIR,))


def _python_if_heavy() -> str:
    return render_template("if_heavy.html", _IF_HEAVY_CONTEXT, directories=(_IF_HEAVY_DIR,))


def _compiled_if_heavy() -> str:
    return _IF_HEAVY_COMPILED.render(dict(_IF_HEAVY_CONTEXT))


def _report(name: str, python_fn, compiled_fn, *, number: int) -> None:
    python_time = timeit.timeit(python_fn, number=number)
    compiled_time = timeit.timeit(compiled_fn, number=number)
    faster = "compiled" if compiled_time < python_time else "interpreter"
    ratio = python_time / compiled_time if compiled_time else float("inf")
    print(f"{name}:")
    print(f"  interpreter: {python_time:.4f}s for {number:,} calls ({python_time / number * 1e6:.3f} us/call)")
    print(f"  compiled:    {compiled_time:.4f}s for {number:,} calls ({compiled_time / number * 1e6:.3f} us/call)")
    print(f"  -> {faster} is faster ({ratio:.2f}x)" if faster == "compiled" else f"  -> {faster} is faster ({1 / ratio:.2f}x)")
    print()


def main() -> None:
    assert _python_page() == _compiled_page(), f"interpreter/compiled page.html output must match:\n{_python_page()!r}\n{_compiled_page()!r}"
    assert _python_if_heavy() == _compiled_if_heavy(), f"interpreter/compiled if_heavy output must match:\n{_python_if_heavy()!r}\n{_compiled_if_heavy()!r}"

    _report("page.html (variables + for, 20 rows)", _python_page, _compiled_page, number=20_000)
    _report("if_heavy.html (if/elif/else + for, 20 rows)", _python_if_heavy, _compiled_if_heavy, number=20_000)


if __name__ == "__main__":
    main()
