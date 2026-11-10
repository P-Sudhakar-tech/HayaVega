"""Benchmark the fix for templates.py's `_render_for`: it used to build a
full `dict(context)` copy on every loop iteration just to inject the loop
variable and `loop` metadata. Two alternatives were tried:

1. `collections.ChainMap` (layers a small overrides dict on the shared
   context instead of copying it) -- measured SLOWER, not faster: its
   per-key lookup overhead applies to every `{{ }}`/condition evaluated
   inside the loop, not just once per iteration, and that cost outweighed
   the copy it avoided. Reverted; kept here as a documented dead end.
2. Mutate `context` in place for the loop variable and `loop` metadata,
   restoring the previous values once after the whole loop finishes --
   the fix actually shipped. See `_render_for` in `hayavega/templates.py`.

Measures both speed and peak memory (tracemalloc, stdlib -- no new
dependency) for the actual shipped `render_template()` (the fix, already
applied) against a private reimplementation of the exact old dict-copy
behavior kept here only for this comparison.

Usage:
    python benchmarks/template_context_fix.py
"""

from __future__ import annotations

import sys
import timeit
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hayavega.templates import _UNDEFINED as UNDEFINED
from hayavega.templates import _evaluate as evaluate
from hayavega.templates import _ForNode, _IfNode, _RenderState, _resolve_chain, _TextNode, _VariableNode, render_template
from hayavega.templates import _render_if as render_if

_TEMPLATE_DIR = Path(__file__).resolve().parent / "hayavega_app" / "templates"
_CONTEXT = {"posts": [{"title": f"Post {i}", "views": i * 3} for i in range(20)]}

# A wider context, to make the old O(len(context)) copy cost visible even
# though the actual production render call only ever passes {"posts": [...]}
# -- this simulates a project whose base template context carries more
# shared data (site settings, current user, nav links) alongside the loop.
_WIDE_CONTEXT = {**_CONTEXT, **{f"setting_{i}": f"value_{i}" for i in range(50)}}


def _render_for_old(node: _ForNode, context: dict, state: _RenderState) -> str:
    """Exact reimplementation of the pre-fix `_render_for`, kept only for this comparison."""
    iterable = evaluate(node.iterable, context, state.template_name, node.line, state.debug)
    if iterable is UNDEFINED or iterable in (None, ""):
        items = []
    else:
        items = list(iterable)
    count = len(items)
    parts = []
    for index, item in enumerate(items):
        loop_context = dict(context)  # the O(len(context)) copy this benchmark measures
        loop_context[node.var_name] = item
        loop_context["loop"] = {"index": index + 1, "index0": index, "first": index == 0, "last": index == count - 1}
        parts.append(_render_nodes_old(node.body, loop_context, state))
    return "".join(parts)


def _render_nodes_old(nodes: list, context: dict, state: _RenderState) -> str:
    from html import escape

    parts = []
    for node in nodes:
        if isinstance(node, _TextNode):
            parts.append(node.text)
        elif isinstance(node, _VariableNode):
            value = evaluate(node.expr, context, state.template_name, node.line, state.debug)
            parts.append("" if value is UNDEFINED else escape(str(value)))
        elif isinstance(node, _IfNode):
            parts.append(render_if(node, context, state))
        elif isinstance(node, _ForNode):
            parts.append(_render_for_old(node, context, state))
    return "".join(parts)


def _render_template_old(template_name: str, context: dict, directories: tuple[Path, ...]) -> str:
    root, blocks = _resolve_chain(template_name, directories)
    state = _RenderState(template_name=template_name, debug=False, blocks=blocks)
    return _render_nodes_old(root.body, dict(context), state)


def _old_page(context: dict) -> str:
    return _render_template_old("page.html", context, (_TEMPLATE_DIR,))


def _new_page(context: dict) -> str:
    return render_template("page.html", context, directories=(_TEMPLATE_DIR,))


def _report_speed(name: str, old_fn, new_fn, *, number: int) -> None:
    old_time = timeit.timeit(old_fn, number=number)
    new_time = timeit.timeit(new_fn, number=number)
    faster = "new (mutate+restore)" if new_time < old_time else "old (dict copy)"
    ratio = old_time / new_time if new_time else float("inf")
    print(f"{name}:")
    print(f"  old (dict copy):      {old_time:.4f}s for {number:,} calls ({old_time / number * 1e6:.3f} us/call)")
    print(f"  new (mutate+restore): {new_time:.4f}s for {number:,} calls ({new_time / number * 1e6:.3f} us/call)")
    print(f"  -> {faster} is faster ({ratio:.2f}x)" if faster.startswith("new") else f"  -> {faster} is faster ({1 / ratio:.2f}x)")
    print()


def _report_memory(name: str, fn, *, calls: int) -> None:
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    for _ in range(calls):
        fn()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"{name} ({calls:,} calls):")
    print(f"  peak traced allocation: {peak / 1024:.1f} KiB (net of {baseline_current / 1024:.1f} KiB baseline)")
    print()


def main() -> None:
    assert _old_page(_CONTEXT) == _new_page(_CONTEXT), "old/new output must match byte-for-byte"
    assert _old_page(_WIDE_CONTEXT) == _new_page(_WIDE_CONTEXT), "old/new output must match on a wide context too"

    print("=== Speed: narrow context (just {'posts': [...]}) ===\n")
    _report_speed("page.html, 20 rows", lambda: _old_page(_CONTEXT), lambda: _new_page(_CONTEXT), number=20_000)

    print("=== Speed: wide context (50 extra shared keys, e.g. site settings) ===\n")
    _report_speed("page.html, 20 rows, 52-key context", lambda: _old_page(_WIDE_CONTEXT), lambda: _new_page(_WIDE_CONTEXT), number=20_000)

    print("=== Peak memory: narrow context ===\n")
    _report_memory("old (dict copy)", lambda: _old_page(_CONTEXT), calls=2_000)
    _report_memory("new (mutate+restore)", lambda: _new_page(_CONTEXT), calls=2_000)

    print("=== Peak memory: wide context (52 keys) ===\n")
    _report_memory("old (dict copy)", lambda: _old_page(_WIDE_CONTEXT), calls=2_000)
    _report_memory("new (mutate+restore)", lambda: _new_page(_WIDE_CONTEXT), calls=2_000)


if __name__ == "__main__":
    main()
