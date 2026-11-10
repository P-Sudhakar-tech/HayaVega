"""Benchmark a *general* native Rust template engine against Python's real
`render_template()` -- the follow-up scoped by native_template_and_asgi.py's
finding that a hand-compiled, one-template-only Rust function beat Python by
16-20x, which was a scoping signal, not proof a general engine would.

This compiles the same already-parsed node tree `hayavega.templates` produces
(via its private `_compile_cached`) into a plain nested-tuple "wire format"
hayavega_native.render_compiled_template can decode, then renders it in Rust
and compares against calling `render_template()` normally. The wire
converter refuses (raises `_Unsupported`) for `{% extends %}`/`{% block %}`,
which the native engine does not implement -- see
`crates/hayavega-core/src/lib.rs`'s template module docstring. Both benchmark
templates here (page.html and a synthetic if/for-heavier one) avoid them.

Requires hayavega_native to be built (see hatch_build.py / README.md).

Usage:
    python benchmarks/native_template_engine.py
"""

from __future__ import annotations

import sys
import timeit
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from hayavega_native import CompiledNativeTemplate, render_compiled_template
except ImportError as error:
    raise SystemExit(f"hayavega_native is not built: {error}. See README.md's 'Building the Rust-backed dispatcher' section.") from error

from hayavega.templates import (
    _BlockNode,
    _BoolOp,
    _Compare,
    _compile_cached,
    _ForNode,
    _IfNode,
    _Literal,
    _Not,
    _Path,
    _TextNode,
    _VariableNode,
    render_template,
)


class _Unsupported(Exception):
    """Raised when a template uses a construct the native engine doesn't implement."""


def _wire_expr(expr) -> tuple:
    if isinstance(expr, _Literal):
        return ("lit", expr.value)
    if isinstance(expr, _Path):
        return ("path", expr.dotted)
    if isinstance(expr, _Not):
        return ("not", _wire_expr(expr.operand))
    if isinstance(expr, _Compare):
        return ("cmp", _wire_expr(expr.left), expr.op, _wire_expr(expr.right))
    if isinstance(expr, _BoolOp):
        return ("bool", expr.op, tuple(_wire_expr(operand) for operand in expr.operands))
    raise _Unsupported(f"unknown expression node: {expr!r}")


def _wire_nodes(nodes: list) -> tuple:
    wire = []
    for node in nodes:
        if isinstance(node, _TextNode):
            wire.append(("text", node.text))
        elif isinstance(node, _VariableNode):
            wire.append(("var", _wire_expr(node.expr)))
        elif isinstance(node, _IfNode):
            branches = tuple((None if condition is None else _wire_expr(condition), _wire_nodes(body)) for condition, body, _line in node.branches)
            wire.append(("if", branches))
        elif isinstance(node, _ForNode):
            wire.append(("for", node.var_name, _wire_expr(node.iterable), _wire_nodes(node.body)))
        elif isinstance(node, _BlockNode):
            raise _Unsupported("{% block %}/{% extends %} are not supported by the native template engine")
        else:
            raise _Unsupported(f"unknown node: {node!r}")
    return tuple(wire)


def compile_to_wire(template_name: str, directories: tuple[Path, ...]) -> tuple:
    """Compile a template file to wire format. Raises _Unsupported if it uses extends/block."""
    compiled = _compile_cached(template_name, directories)
    if compiled.extends is not None:
        raise _Unsupported("{% extends %} is not supported by the native template engine")
    return _wire_nodes(compiled.body)


# --- Benchmark 1: the real benchmark suite's page.html (variables + for) ---

_TEMPLATE_DIR = Path(__file__).resolve().parent / "hayavega_app" / "templates"
_POSTS_CONTEXT = {"posts": [{"title": f"Post {i}", "views": i * 3} for i in range(20)]}
_PAGE_WIRE = compile_to_wire("page.html", (_TEMPLATE_DIR,))


def _python_page() -> str:
    return render_template("page.html", _POSTS_CONTEXT, directories=(_TEMPLATE_DIR,))


def _native_page() -> str:
    return render_compiled_template(_PAGE_WIRE, _POSTS_CONTEXT)


_PAGE_COMPILED = CompiledNativeTemplate(_PAGE_WIRE)


def _native_page_cached() -> str:
    return _PAGE_COMPILED.render(_POSTS_CONTEXT)


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


def _make_if_heavy_template(tmp_dir: Path) -> None:
    (tmp_dir / "if_heavy.html").write_text(_IF_HEAVY_SOURCE, encoding="utf-8")


_IF_HEAVY_DIR = Path(__file__).resolve().parent / "native_template_engine_fixtures"
_IF_HEAVY_DIR.mkdir(exist_ok=True)
_make_if_heavy_template(_IF_HEAVY_DIR)
_IF_HEAVY_CONTEXT = {"posts": [{"title": f"Post {i}", "views": i * 3, "featured": i % 5 == 0} for i in range(20)]}
_IF_HEAVY_WIRE = compile_to_wire("if_heavy.html", (_IF_HEAVY_DIR,))


def _python_if_heavy() -> str:
    return render_template("if_heavy.html", _IF_HEAVY_CONTEXT, directories=(_IF_HEAVY_DIR,))


def _native_if_heavy() -> str:
    return render_compiled_template(_IF_HEAVY_WIRE, _IF_HEAVY_CONTEXT)


def _report(name: str, python_fn, native_fn, *, number: int) -> None:
    python_time = timeit.timeit(python_fn, number=number)
    native_time = timeit.timeit(native_fn, number=number)
    faster = "native" if native_time < python_time else "python"
    ratio = python_time / native_time if native_time else float("inf")
    print(f"{name}:")
    print(f"  python: {python_time:.4f}s for {number:,} calls ({python_time / number * 1e6:.3f} us/call)")
    print(f"  native: {native_time:.4f}s for {number:,} calls ({native_time / number * 1e6:.3f} us/call)")
    print(f"  -> {faster} is faster ({ratio:.2f}x)" if faster == "native" else f"  -> {faster} is faster ({1 / ratio:.2f}x)")
    print()


def main() -> None:
    assert _python_page() == _native_page(), "python/native page.html output must match byte-for-byte"
    assert _python_page() == _native_page_cached(), "python/cached-native page.html output must match byte-for-byte"
    assert _python_if_heavy() == _native_if_heavy(), f"python/native if_heavy output must match:\n{_python_if_heavy()!r}\n{_native_if_heavy()!r}"

    _report("general engine: page.html (variables + for, 20 rows)", _python_page, _native_page, number=20_000)
    _report("general engine, wire-decode cached once: page.html", _python_page, _native_page_cached, number=20_000)
    _report("general engine: if_heavy.html (if/elif/else + for, 20 rows)", _python_if_heavy, _native_if_heavy, number=20_000)


if __name__ == "__main__":
    main()
