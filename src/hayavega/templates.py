"""Small, safe HTML template renderer for server-rendered views.

Supports variable interpolation (``{{ expr }}``, HTML-escaped by default),
``{% if/elif/else %}`` conditionals, ``{% for x in expr %}`` loops, and
single-chain ``{% extends %}``/``{% block %}`` inheritance. Expressions are
limited to dotted-path lookups, literals, and a small set of comparison/
boolean operators. The guarantee that matters is: **no template ever
executes anything a caller supplies** -- there is no ``eval`` of template
source, request data, or any other caller-controlled string, at any
optimization level described below.

Templates are parsed once and cached per file (invalidated by mtime), so
repeated renders of an unchanged file skip re-reading and re-parsing it.
Templates that use only variables/if/for (no ``{% extends %}``/
``{% block %}``) additionally get compiled once into a real Python
function via ``compile()``/``exec()`` on that already-parsed, trusted node
tree -- never on a caller-supplied string -- so repeated renders call a
plain function instead of re-walking the node tree, matching the technique
Jinja2/Mako use internally. Measured 1.4x-2.2x faster than the pure
tree-walking interpreter (see ``benchmarks/compiled_template_engine.py``);
templates using ``{% extends %}``/``{% block %}`` transparently fall back
to the interpreter, which remains fully supported and unchanged.
"""

from __future__ import annotations

import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from pathlib import Path
from threading import Lock
from typing import Any


class TemplateNotFound(FileNotFoundError):
    pass


class TemplateSyntaxError(ValueError):
    """Raised for a malformed template; the message includes name and line."""


class TemplateRenderError(RuntimeError):
    """Raised in debug mode for an undefined variable, attribute, or block."""


_UNDEFINED = object()

# ---------------------------------------------------------------------------
# Expressions: dotted paths, literals, comparisons, and boolean operators.
# ---------------------------------------------------------------------------

_EXPR_TOKEN = re.compile(
    r"""\s*(?:
        (?P<string>'[^']*'|"[^"]*")
      | (?P<number>-?\d+(?:\.\d+)?)
      | (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
      | (?P<op>==|!=)
    )""",
    re.VERBOSE,
)

_KEYWORDS = {"and", "or", "not", "in", "true", "false", "none"}


@dataclass
class _Literal:
    value: object


@dataclass
class _Path:
    dotted: str
    parts: tuple[str, ...]


@dataclass
class _Not:
    operand: object


@dataclass
class _Compare:
    left: object
    op: str
    right: object


@dataclass
class _BoolOp:
    op: str
    operands: list


def _tokenize_expression(text: str, template_name: str, line: int) -> list[tuple[str, str]]:
    tokens = []
    pos, n = 0, len(text)
    while pos < n:
        match = _EXPR_TOKEN.match(text, pos)
        if not match or match.end() == pos:
            if text[pos:].strip() == "":
                break
            raise TemplateSyntaxError(f"{template_name}:{line}: cannot parse expression {text!r}.")
        pos = match.end()
        for kind in ("string", "number", "word", "op"):
            value = match.group(kind)
            if value is not None:
                tokens.append((kind, value))
                break
    return tokens


class _ExpressionParser:
    def __init__(self, tokens: list[tuple[str, str]], template_name: str, line: int) -> None:
        self._tokens = tokens
        self._pos = 0
        self._template_name = template_name
        self._line = line

    def parse(self):
        if not self._tokens:
            raise TemplateSyntaxError(f"{self._template_name}:{self._line}: expected an expression.")
        node = self._or_expr()
        if self._pos != len(self._tokens):
            raise TemplateSyntaxError(f"{self._template_name}:{self._line}: unexpected token {self._tokens[self._pos][1]!r}.")
        return node

    def _peek_word(self, word: str) -> bool:
        return self._pos < len(self._tokens) and self._tokens[self._pos] == ("word", word)

    def _or_expr(self):
        operands = [self._and_expr()]
        while self._peek_word("or"):
            self._pos += 1
            operands.append(self._and_expr())
        return operands[0] if len(operands) == 1 else _BoolOp("or", operands)

    def _and_expr(self):
        operands = [self._not_expr()]
        while self._peek_word("and"):
            self._pos += 1
            operands.append(self._not_expr())
        return operands[0] if len(operands) == 1 else _BoolOp("and", operands)

    def _not_expr(self):
        if self._peek_word("not"):
            self._pos += 1
            return _Not(self._not_expr())
        return self._comparison()

    def _comparison(self):
        left = self._atom()
        if self._pos < len(self._tokens):
            kind, value = self._tokens[self._pos]
            if (kind == "op" and value in ("==", "!=")) or (kind == "word" and value == "in"):
                self._pos += 1
                return _Compare(left, value, self._atom())
        return left

    def _atom(self):
        if self._pos >= len(self._tokens):
            raise TemplateSyntaxError(f"{self._template_name}:{self._line}: expected a value.")
        kind, value = self._tokens[self._pos]
        self._pos += 1
        if kind == "string":
            return _Literal(value[1:-1])
        if kind == "number":
            return _Literal(float(value) if "." in value else int(value))
        if kind == "word":
            if value == "true":
                return _Literal(True)
            if value == "false":
                return _Literal(False)
            if value == "none":
                return _Literal(None)
            if value in _KEYWORDS:
                raise TemplateSyntaxError(f"{self._template_name}:{self._line}: unexpected keyword '{value}'.")
            return _Path(value, tuple(value.split(".")))
        raise TemplateSyntaxError(f"{self._template_name}:{self._line}: unexpected token {value!r}.")


def _parse_expression(text: str, template_name: str, line: int):
    return _ExpressionParser(_tokenize_expression(text, template_name, line), template_name, line).parse()


def _resolve_attr(value, name: str):
    if isinstance(value, dict):
        return value.get(name, _UNDEFINED)
    if hasattr(value, name):
        return getattr(value, name)
    return _UNDEFINED


def _resolve_path(context: dict, path: _Path, template_name: str, line: int, debug: bool):
    parts = path.parts
    current = context.get(parts[0], _UNDEFINED)
    if current is not _UNDEFINED:
        for part in parts[1:]:
            current = _resolve_attr(current, part)
            if current is _UNDEFINED:
                break
    if current is _UNDEFINED and debug:
        raise TemplateRenderError(f"{template_name}:{line}: '{path.dotted}' is not defined.")
    return current


def _truthy(value) -> bool:
    return False if value is _UNDEFINED else bool(value)


def _as_comparable(value):
    """An undefined path compares like ``none`` without ever displaying as it."""
    return None if value is _UNDEFINED else value


def _evaluate(expr, context: dict, template_name: str, line: int, debug: bool):
    if isinstance(expr, _Literal):
        return expr.value
    if isinstance(expr, _Path):
        return _resolve_path(context, expr, template_name, line, debug)
    if isinstance(expr, _Not):
        return not _truthy(_evaluate(expr.operand, context, template_name, line, debug))
    if isinstance(expr, _Compare):
        left = _as_comparable(_evaluate(expr.left, context, template_name, line, debug))
        right = _as_comparable(_evaluate(expr.right, context, template_name, line, debug))
        if expr.op == "==":
            return left == right
        if expr.op == "!=":
            return left != right
        try:
            return left in right
        except TypeError:
            return False
    if isinstance(expr, _BoolOp):
        values = (_evaluate(operand, context, template_name, line, debug) for operand in expr.operands)
        return all(_truthy(v) for v in values) if expr.op == "and" else any(_truthy(v) for v in values)
    raise AssertionError(expr)  # pragma: no cover


# ---------------------------------------------------------------------------
# Tags: {% if/elif/else/endif %}, {% for/endfor %}, {% block/endblock %}, {% extends %}
# ---------------------------------------------------------------------------

_SEGMENT = re.compile(r"\{\{(.*?)\}\}|\{%(.*?)%\}", re.DOTALL)


@dataclass
class _TextNode:
    text: str


@dataclass
class _VariableNode:
    expr: object
    line: int


@dataclass
class _IfNode:
    branches: list  # list of (expr_or_None, body, line)


@dataclass
class _ForNode:
    var_name: str
    iterable: object
    body: list
    line: int


@dataclass
class _BlockNode:
    name: str
    body: list


@dataclass
class _CompiledTemplate:
    extends: str | None
    body: list
    # Populated lazily by _compiled_render_for(); compiled_render_attempted
    # distinguishes "not tried yet" from "tried and unsupported" (None) so
    # a {% block %}-using template doesn't re-attempt compilation on every
    # render.
    compiled_render: Callable[[dict, bool], str] | None = None
    compiled_render_attempted: bool = False


def _scan(source: str) -> list[tuple[str, str, int]]:
    segments = []
    pos, line = 0, 1
    for match in _SEGMENT.finditer(source):
        if match.start() > pos:
            text = source[pos : match.start()]
            segments.append(("text", text, line))
            line += text.count("\n")
        variable, tag = match.group(1), match.group(2)
        if variable is not None:
            segments.append(("var", variable.strip(), line))
        else:
            segments.append(("tag", tag.strip(), line))
        line += match.group(0).count("\n")
        pos = match.end()
    if pos < len(source):
        segments.append(("text", source[pos:], line))
    return segments


class _TemplateParser:
    def __init__(self, segments: list[tuple[str, str, int]], template_name: str) -> None:
        self._segments = segments
        self._pos = 0
        self._template_name = template_name

    def _current(self, from_line: int, expected: str) -> tuple[str, str, int]:
        if self._pos >= len(self._segments):
            raise TemplateSyntaxError(f"{self._template_name}:{from_line}: unexpected end of template, expected '{expected}'.")
        return self._segments[self._pos]

    def parse(self) -> _CompiledTemplate:
        extends = None
        if self._segments and self._segments[0][0] == "tag" and self._segments[0][1].split(None, 1)[0] == "extends":
            _, tag, line = self._segments[0]
            extends = self._parse_extends_target(tag, line)
            self._pos = 1
        body = self._parse_nodes(stop_tags=())
        if self._pos != len(self._segments):
            _, tag, line = self._segments[self._pos]
            raise TemplateSyntaxError(f"{self._template_name}:{line}: unexpected '{{% {tag} %}}'.")
        return _CompiledTemplate(extends=extends, body=body)

    def _parse_extends_target(self, tag: str, line: int) -> str:
        rest = tag[len("extends") :].strip()
        if len(rest) < 2 or rest[0] != rest[-1] or rest[0] not in "\"'":
            raise TemplateSyntaxError(f"{self._template_name}:{line}: 'extends' requires a quoted template name.")
        return rest[1:-1]

    def _parse_nodes(self, stop_tags: tuple[str, ...]) -> list:
        nodes: list[Any] = []
        while self._pos < len(self._segments):
            kind, value, line = self._segments[self._pos]
            if kind == "text":
                nodes.append(_TextNode(value))
                self._pos += 1
                continue
            if kind == "var":
                nodes.append(_VariableNode(_parse_expression(value, self._template_name, line), line))
                self._pos += 1
                continue
            tag_name = value.split(None, 1)[0] if value else ""
            if tag_name in stop_tags:
                return nodes
            if tag_name == "if":
                nodes.append(self._parse_if(value, line))
                continue
            if tag_name == "for":
                nodes.append(self._parse_for(value, line))
                continue
            if tag_name == "block":
                nodes.append(self._parse_block(value, line))
                continue
            if tag_name == "extends":
                raise TemplateSyntaxError(f"{self._template_name}:{line}: 'extends' must be the first tag in the template.")
            if tag_name in ("elif", "else", "endif", "endfor", "endblock"):
                raise TemplateSyntaxError(f"{self._template_name}:{line}: unexpected '{{% {value} %}}'.")
            raise TemplateSyntaxError(f"{self._template_name}:{line}: unknown tag '{tag_name}'.")
        return nodes

    def _parse_if(self, value: str, line: int) -> _IfNode:
        branches = []
        seen_else = False
        condition = _parse_expression(value[len("if") :].strip(), self._template_name, line)
        self._pos += 1
        branches.append((condition, self._parse_nodes(stop_tags=("elif", "else", "endif")), line))
        while True:
            _, tag_value, tag_line = self._current(line, "endif")
            tag_name = tag_value.split(None, 1)[0]
            if tag_name == "elif":
                if seen_else:
                    raise TemplateSyntaxError(f"{self._template_name}:{tag_line}: 'elif' cannot follow 'else'.")
                condition = _parse_expression(tag_value[len("elif") :].strip(), self._template_name, tag_line)
                self._pos += 1
                branches.append((condition, self._parse_nodes(stop_tags=("elif", "else", "endif")), tag_line))
                continue
            if tag_name == "else":
                if seen_else:
                    raise TemplateSyntaxError(f"{self._template_name}:{tag_line}: multiple 'else' branches.")
                seen_else = True
                self._pos += 1
                branches.append((None, self._parse_nodes(stop_tags=("endif",)), tag_line))
                continue
            if tag_name == "endif":
                self._pos += 1
                return _IfNode(branches)
            raise TemplateSyntaxError(f"{self._template_name}:{tag_line}: expected 'elif', 'else', or 'endif'.")

    def _parse_for(self, value: str, line: int) -> _ForNode:
        rest = value[len("for") :].strip()
        if " in " not in rest:
            raise TemplateSyntaxError(f"{self._template_name}:{line}: 'for' requires 'for <name> in <expr>'.")
        var_name, _, iterable_expr = rest.partition(" in ")
        var_name = var_name.strip()
        if not var_name.isidentifier():
            raise TemplateSyntaxError(f"{self._template_name}:{line}: invalid loop variable name '{var_name}'.")
        iterable = _parse_expression(iterable_expr.strip(), self._template_name, line)
        self._pos += 1
        body = self._parse_nodes(stop_tags=("endfor",))
        _, tag_value, tag_line = self._current(line, "endfor")
        if tag_value.split(None, 1)[0] != "endfor":
            raise TemplateSyntaxError(f"{self._template_name}:{tag_line}: expected 'endfor'.")
        self._pos += 1
        return _ForNode(var_name, iterable, body, line)

    def _parse_block(self, value: str, line: int) -> _BlockNode:
        name = value[len("block") :].strip()
        if not name.isidentifier():
            raise TemplateSyntaxError(f"{self._template_name}:{line}: invalid block name '{name}'.")
        self._pos += 1
        body = self._parse_nodes(stop_tags=("endblock",))
        _, tag_value, tag_line = self._current(line, "endblock")
        if tag_value.split(None, 1)[0] != "endblock":
            raise TemplateSyntaxError(f"{self._template_name}:{tag_line}: expected 'endblock'.")
        self._pos += 1
        return _BlockNode(name, body)


@dataclass
class _RenderState:
    template_name: str
    debug: bool
    blocks: dict


def _render_nodes(nodes: list, context: dict, state: _RenderState) -> str:
    parts = []
    for node in nodes:
        if isinstance(node, _TextNode):
            parts.append(node.text)
        elif isinstance(node, _VariableNode):
            value = _evaluate(node.expr, context, state.template_name, node.line, state.debug)
            parts.append("" if value is _UNDEFINED else escape(str(value)))
        elif isinstance(node, _IfNode):
            parts.append(_render_if(node, context, state))
        elif isinstance(node, _ForNode):
            parts.append(_render_for(node, context, state))
        elif isinstance(node, _BlockNode):
            parts.append(_render_nodes(state.blocks.get(node.name, node.body), context, state))
        else:
            raise AssertionError(node)  # pragma: no cover
    return "".join(parts)


def _render_if(node: _IfNode, context: dict, state: _RenderState) -> str:
    for condition, body, line in node.branches:
        if condition is None or _truthy(_evaluate(condition, context, state.template_name, line, state.debug)):
            return _render_nodes(body, context, state)
    return ""


def _render_for(node: _ForNode, context: dict, state: _RenderState) -> str:
    iterable = _evaluate(node.iterable, context, state.template_name, node.line, state.debug)
    if iterable is _UNDEFINED or iterable in (None, ""):
        items = []
    else:
        try:
            items = list(iterable)
        except TypeError as exc:
            if state.debug:
                raise TemplateRenderError(f"{state.template_name}:{node.line}: the value bound to '{node.var_name}' is not iterable.") from exc
            items = []
    count = len(items)
    parts = []
    # Mutate `context` in place for the loop variable and `loop` metadata,
    # restoring whatever was there before once the whole loop finishes,
    # instead of copying the full context on every iteration (measured
    # slower, see benchmarks/template_context_fix.py) or wrapping it in a
    # ChainMap (measured slower still -- its per-key lookup overhead on
    # every {{ }}/condition outweighs the copy it avoids). This is safe
    # because `context` here is always a private dict this render created
    # (render_template's own `dict(context or {})`), never the caller's
    # original object, and nested loops each save/restore their own prior
    # values correctly by construction.
    had_var, previous_var = (node.var_name in context, context.get(node.var_name))
    had_loop, previous_loop = ("loop" in context, context.get("loop"))
    try:
        for index, item in enumerate(items):
            context[node.var_name] = item
            context["loop"] = {"index": index + 1, "index0": index, "first": index == 0, "last": index == count - 1}
            parts.append(_render_nodes(node.body, context, state))
    finally:
        if had_var:
            context[node.var_name] = previous_var
        else:
            context.pop(node.var_name, None)
        if had_loop:
            context["loop"] = previous_loop
        else:
            context.pop("loop", None)
    return "".join(parts)


def _collect_blocks(nodes: list, blocks: dict) -> None:
    for node in nodes:
        if isinstance(node, _BlockNode):
            blocks.setdefault(node.name, node.body)
        elif isinstance(node, _IfNode):
            for _, body, _line in node.branches:
                _collect_blocks(body, blocks)
        elif isinstance(node, _ForNode):
            _collect_blocks(node.body, blocks)


# ---------------------------------------------------------------------------
# Compiling a parsed, extends/block-free node tree into a real Python
# function via compile()/exec() -- see the module docstring for the
# guarantee this preserves and benchmarks/compiled_template_engine.py for
# the measurements that justified adding it.
#
# Node-level structure (text/if/elif/else/for) becomes real Python control
# flow, removing the per-render isinstance() dispatch _render_nodes does on
# every node. Expression evaluation ({{ }}, if/for conditions) still calls
# the existing, tested _evaluate() interpreter -- deliberately not compiled
# to raw operators, since _evaluate()'s undefined-vs-None semantics and the
# `in` operator's TypeError->False fallback are exactly the kind of subtle
# behavior a reimplementation could get wrong for no benchmarked gain.
#
# Safety: template TEXT is embedded via repr(), which safely escapes any
# quotes/backslashes/control characters -- never via string interpolation
# into source. Expression AST objects are never stringified into source at
# all: they live in a plain Python list built at compile time and are
# referenced by index (_E[3]) in the generated code.
# ---------------------------------------------------------------------------


class _UncompilableTemplate(Exception):
    """Raised internally when a node tree can't be compiled (uses {% block %}); always caught."""


def _compile_expr_pool(nodes: list, pool: list, indent: int, template_name: str) -> list[str]:
    pad = "    " * indent
    lines: list[str] = []
    for node in nodes:
        if isinstance(node, _TextNode):
            lines.append(f"{pad}_out.append({node.text!r})")
        elif isinstance(node, _VariableNode):
            index = len(pool)
            pool.append(node.expr)
            lines.append(f"{pad}_v = _evaluate(_E[{index}], context, _NAME, {node.line}, debug)")
            lines.append(f"{pad}if _v is not _UNDEFINED:")
            lines.append(f"{pad}    _out.append(escape(str(_v)))")
        elif isinstance(node, _IfNode):
            for position, (condition, body, line) in enumerate(node.branches):
                body_lines = _compile_expr_pool(body, pool, indent + 1, template_name) or [f"{pad}    pass"]
                if condition is None:
                    lines.append(f"{pad}else:")
                else:
                    index = len(pool)
                    pool.append(condition)
                    keyword = "if" if position == 0 else "elif"
                    lines.append(f"{pad}{keyword} _truthy(_evaluate(_E[{index}], context, _NAME, {line}, debug)):")
                lines.extend(body_lines)
        elif isinstance(node, _ForNode):
            var = node.var_name
            index = len(pool)
            pool.append(node.iterable)
            not_iterable_message = f"{template_name}:{node.line}: the value bound to '{var}' is not iterable."
            lines.append(f"{pad}_iterable = _evaluate(_E[{index}], context, _NAME, {node.line}, debug)")
            lines.append(f"{pad}if _iterable is _UNDEFINED or _iterable in (None, ''):")
            lines.append(f"{pad}    _items = []")
            lines.append(f"{pad}else:")
            lines.append(f"{pad}    try:")
            lines.append(f"{pad}        _items = list(_iterable)")
            lines.append(f"{pad}    except TypeError as _exc:")
            lines.append(f"{pad}        if debug:")
            lines.append(f"{pad}            raise TemplateRenderError({not_iterable_message!r}) from _exc")
            lines.append(f"{pad}        _items = []")
            lines.append(f"{pad}_count = len(_items)")
            lines.append(f"{pad}_had_var = {var!r} in context")
            lines.append(f"{pad}_prev_var = context.get({var!r})")
            lines.append(f"{pad}_had_loop = 'loop' in context")
            lines.append(f"{pad}_prev_loop = context.get('loop')")
            lines.append(f"{pad}for _index, _item in enumerate(_items):")
            lines.append(f"{pad}    context[{var!r}] = _item")
            lines.append(f"{pad}    context['loop'] = {{'index': _index + 1, 'index0': _index, 'first': _index == 0, 'last': _index + 1 == _count}}")
            lines.extend(_compile_expr_pool(node.body, pool, indent + 1, template_name) or [f"{pad}    pass"])
            lines.append(f"{pad}if _had_var:")
            lines.append(f"{pad}    context[{var!r}] = _prev_var")
            lines.append(f"{pad}else:")
            lines.append(f"{pad}    context.pop({var!r}, None)")
            lines.append(f"{pad}if _had_loop:")
            lines.append(f"{pad}    context['loop'] = _prev_loop")
            lines.append(f"{pad}else:")
            lines.append(f"{pad}    context.pop('loop', None)")
        elif isinstance(node, _BlockNode):
            raise _UncompilableTemplate("{% block %} is not supported by the compiled render path")
        else:
            raise AssertionError(node)  # pragma: no cover
    return lines


def _build_compiled_render(nodes: list, template_name: str) -> Callable[[dict, bool], str] | None:
    """Compile ``nodes`` into a real function, or return ``None`` if it uses
    ``{% block %}`` (the interpreter handles that case; see ``render_template``)."""
    pool: list = []
    try:
        body_lines = _compile_expr_pool(nodes, pool, 1, template_name)
    except _UncompilableTemplate:
        return None
    source = "\n".join(["def _render(context, debug):", "    _out = []", *body_lines, "    return ''.join(_out)"])
    namespace: dict[str, Any] = {"_evaluate": _evaluate, "_truthy": _truthy, "_UNDEFINED": _UNDEFINED, "_E": pool, "_NAME": template_name, "escape": escape, "TemplateRenderError": TemplateRenderError}
    code = compile(source, f"<compiled template {template_name!r}>", "exec")
    exec(code, namespace)  # noqa: S102 -- `source` is generated entirely by this module from an already-parsed, trusted node tree, never from caller-supplied text; see module docstring.
    return namespace["_render"]


def _compiled_render_for(root: _CompiledTemplate, template_name: str) -> Callable[[dict, bool], str] | None:
    """Compile-once-per-cached-template-object cache for the function
    ``_build_compiled_render`` produces. Attached directly to the same
    ``_CompiledTemplate`` instance ``_compile_cached`` already caches by
    file mtime, rather than a separate identity-keyed dict: when a file
    changes, ``_compile_cached`` hands back a brand-new object with
    ``compiled_render_attempted=False``, so this cache is invalidated for
    free with no risk of a stale identity-based key being reused."""
    if root.compiled_render_attempted:
        return root.compiled_render
    with _cache_lock:
        if root.compiled_render_attempted:
            return root.compiled_render
        root.compiled_render = _build_compiled_render(root.body, template_name)
        root.compiled_render_attempted = True
    return root.compiled_render


# ---------------------------------------------------------------------------
# File loading and caching
# ---------------------------------------------------------------------------

_cache_lock = Lock()
_template_cache: dict[Path, tuple[float, _CompiledTemplate]] = {}


def _find_template_file(template_name: str, directories) -> tuple[Path, float]:
    """Returns the matching path and its mtime from the same `stat()` call
    that locates it, instead of a separate `is_file()` check (one syscall)
    followed by the caller's own `.stat()` (a second syscall on the exact
    same file) -- found via `cProfile` showing `nt.stat` at a real,
    measurable share of `template_render`'s per-request cost."""
    if Path(template_name).is_absolute() or ".." in Path(template_name).parts:
        raise ValueError("Template paths must stay inside a template directory.")
    for directory in map(Path, directories):
        candidate = directory / template_name
        try:
            result = candidate.stat()
        except OSError:
            continue
        if stat.S_ISREG(result.st_mode):
            return candidate, result.st_mtime
    raise TemplateNotFound(template_name)


def _compile_cached(template_name: str, directories) -> _CompiledTemplate:
    path, mtime = _find_template_file(template_name, directories)
    with _cache_lock:
        cached = _template_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    compiled = _TemplateParser(_scan(path.read_text(encoding="utf-8")), template_name).parse()
    with _cache_lock:
        _template_cache[path] = (mtime, compiled)
    return compiled


def _resolve_chain(template_name: str, directories) -> tuple[_CompiledTemplate, dict]:
    blocks: dict[str, list] = {}
    seen = set()
    current_name = template_name
    while True:
        if current_name in seen:
            raise TemplateSyntaxError(f"{template_name}: circular 'extends' chain involving '{current_name}'.")
        seen.add(current_name)
        compiled = _compile_cached(current_name, directories)
        _collect_blocks(compiled.body, blocks)
        if compiled.extends is None:
            return compiled, blocks
        current_name = compiled.extends


def render_template(template_name: str, context: dict | None = None, *, directories=(), debug: bool = False) -> str:
    """Render ``template_name`` with variables, conditionals, loops, and inheritance.

    Every interpolated value is HTML-escaped and no template code executes
    arbitrary Python. Set ``debug=True`` during development to raise
    ``TemplateRenderError``/``TemplateSyntaxError`` on an undefined variable,
    attribute, non-iterable loop target, or block name that the base
    template never defines, instead of silently rendering nothing; leave it
    ``False`` (the default) in production so an unexpected value degrades to
    empty output rather than a 500.
    """
    root, blocks = _resolve_chain(template_name, directories)
    if debug:
        root_blocks: dict = {}
        _collect_blocks(root.body, root_blocks)
        unknown = sorted(set(blocks) - set(root_blocks))
        if unknown:
            raise TemplateSyntaxError(f"{template_name}: block(s) {unknown} are not defined in the base template.")
    compiled_render = _compiled_render_for(root, template_name)
    if compiled_render is not None:
        return compiled_render(dict(context or {}), debug)
    state = _RenderState(template_name=template_name, debug=debug, blocks=blocks)
    return _render_nodes(root.body, dict(context or {}), state)
