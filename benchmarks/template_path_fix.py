"""Benchmark the precomputed-dotted-path-split fix: `_Path` used to store
only the raw dotted string ("post.title"), and `_resolve_path` called
`dotted.split(".")` fresh on every single evaluation. `_Path` now also
stores the pre-split `parts` tuple at parse time, so `_resolve_path` just
indexes/iterates it.

Compares old-vs-new in the same run (same machine conditions), rather than
against a number recorded in a different session -- absolute timeit numbers
are not comparable across sessions/machines/background load, only within
one run.

Usage:
    python benchmarks/template_path_fix.py
"""

from __future__ import annotations

import sys
import timeit
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mangoo.templates import _UNDEFINED, _ForNode, _IfNode, _Path, _RenderState, _resolve_attr, _resolve_chain, _TextNode, _VariableNode, render_template
from mangoo.templates import _evaluate as evaluate
from mangoo.templates import _render_if as render_if

_TEMPLATE_DIR = Path(__file__).resolve().parent / "mangoo_app" / "templates"
_CONTEXT = {"posts": [{"title": f"Post {i}", "views": i * 3} for i in range(20)]}


def _resolve_path_old(context: dict, dotted: str, template_name: str, line: int, debug: bool):
    """Exact reimplementation of the pre-fix `_resolve_path`: splits `dotted` fresh every call."""
    parts = dotted.split(".")
    current = context.get(parts[0], _UNDEFINED)
    if current is not _UNDEFINED:
        for part in parts[1:]:
            current = _resolve_attr(current, part)
            if current is _UNDEFINED:
                break
    if current is _UNDEFINED and debug:
        raise RuntimeError(f"{template_name}:{line}: '{dotted}' is not defined.")
    return current


def _evaluate_old(expr, context: dict, template_name: str, line: int, debug: bool):
    if isinstance(expr, _Path):
        return _resolve_path_old(context, expr.dotted, template_name, line, debug)
    return evaluate(expr, context, template_name, line, debug)


def _render_nodes_old(nodes: list, context: dict, state: _RenderState) -> str:
    from html import escape

    parts = []
    for node in nodes:
        if isinstance(node, _TextNode):
            parts.append(node.text)
        elif isinstance(node, _VariableNode):
            value = _evaluate_old(node.expr, context, state.template_name, node.line, state.debug)
            parts.append("" if value is _UNDEFINED else escape(str(value)))
        elif isinstance(node, _IfNode):
            parts.append(render_if(node, context, state))
        elif isinstance(node, _ForNode):
            parts.append(_render_for_old(node, context, state))
    return "".join(parts)


def _render_for_old(node: _ForNode, context: dict, state: _RenderState) -> str:
    iterable = _evaluate_old(node.iterable, context, state.template_name, node.line, state.debug)
    items = [] if iterable is _UNDEFINED or iterable in (None, "") else list(iterable)
    count = len(items)
    parts = []
    for index, item in enumerate(items):
        context[node.var_name] = item
        context["loop"] = {"index": index + 1, "index0": index, "first": index == 0, "last": index == count - 1}
        parts.append(_render_nodes_old(node.body, context, state))
    return "".join(parts)


def _render_template_old(template_name: str, context: dict, directories: tuple[Path, ...]) -> str:
    root, blocks = _resolve_chain(template_name, directories)
    state = _RenderState(template_name=template_name, debug=False, blocks=blocks)
    return _render_nodes_old(root.body, dict(context), state)


def _old_page() -> str:
    return _render_template_old("page.html", _CONTEXT, (_TEMPLATE_DIR,))


def _new_page() -> str:
    return render_template("page.html", _CONTEXT, directories=(_TEMPLATE_DIR,))


def main() -> None:
    assert _old_page() == _new_page(), "old/new output must match byte-for-byte"

    number = 20_000
    old_time = timeit.timeit(_old_page, number=number)
    new_time = timeit.timeit(_new_page, number=number)
    ratio = old_time / new_time if new_time else float("inf")
    print("Precomputed dotted-path split (same-run comparison):")
    print(f"  old (split every call): {old_time:.4f}s for {number:,} calls ({old_time / number * 1e6:.3f} us/call)")
    print(f"  new (precomputed):      {new_time:.4f}s for {number:,} calls ({new_time / number * 1e6:.3f} us/call)")
    print(f"  -> new is faster ({ratio:.2f}x)" if new_time < old_time else f"  -> old is faster ({1 / ratio:.2f}x)")


if __name__ == "__main__":
    main()
