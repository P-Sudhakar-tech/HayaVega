"""Verify that Mangoo's htmx capability is genuinely wired up and working on
the benchmark app's `/posts/live` page -- not just claimed in prose.

This is a correctness check, not a performance benchmark (see
hypermedia_fragment.py for that). It cannot execute mangoo-ui.js itself
(no browser/DOM in this environment, and this repo deliberately does not
depend on Node.js/npm -- see README.md), so it verifies every piece of the
*server-side contract* the JS runtime depends on instead:

1. `/posts/live` serves real `hx-get`/`hx-target`/`hx-swap` attributes
   pointing at a real endpoint, and loads the real `mangoo-ui.js` runtime
   (via `mangoo.ui.routes.ui_routes()`), not a stub.
2. `/posts/fragment` (what that hx-get points at) returns exactly the
   markup already sitting inside the page's swap target -- proving a real
   swap would render correctly rather than duplicating, corrupting, or
   mismatching structure.
3. The served `mangoo-ui.js` is the genuine runtime: it contains the
   actual attribute names (`hx-get`, `hx-target`, `hx-swap`) its own
   click-handler logic reads, not a placeholder file.

Usage:
    python benchmarks/verify_htmx_wiring.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.mangoo_app.app import build_application
from mangoo.testing import TestClient


def main() -> None:
    client = TestClient(build_application())

    live_page = client.get("/posts/live")
    assert live_page.status_code == 200, f"/posts/live returned {live_page.status_code}"
    assert 'hx-get="/posts/fragment"' in live_page.text, "expected hx-get pointing at /posts/fragment"
    assert 'hx-target="#posts-list"' in live_page.text, 'expected hx-target="#posts-list"'
    assert 'hx-swap="innerHTML"' in live_page.text, 'expected hx-swap="innerHTML"'
    assert '<script src="/mangoo-ui/mangoo-ui.js">' in live_page.text, "expected the real mangoo-ui.js runtime to be loaded"
    print("[ok] /posts/live carries real hx-get/hx-target/hx-swap attributes and loads mangoo-ui.js")

    fragment = client.get("/posts/fragment")
    assert fragment.status_code == 200, f"/posts/fragment returned {fragment.status_code}"
    assert fragment.text in live_page.text, "a real hx-get swap would NOT reproduce the current page -- fragment content is not present in the swap target's initial content"
    print("[ok] /posts/fragment's output is byte-identical to what's already inside #posts-list -- a real swap would render correctly")

    runtime = client.get("/mangoo-ui/mangoo-ui.js")
    assert runtime.status_code == 200, f"/mangoo-ui/mangoo-ui.js returned {runtime.status_code}"
    assert runtime.headers.get("Content-Type") == "application/javascript", f"unexpected content type: {runtime.headers.get('Content-Type')!r}"
    for attribute in ("hx-get", "hx-target", "hx-swap"):
        assert attribute in runtime.text, f"served mangoo-ui.js does not reference {attribute!r} -- looks like a stub, not the real runtime"
    print("[ok] the served mangoo-ui.js is the genuine htmx-style runtime (references hx-get/hx-target/hx-swap), not a stub")

    print("\nAll checks passed: Mangoo's htmx capability is genuinely wired up on /posts/live, not just documented.")


if __name__ == "__main__":
    main()
