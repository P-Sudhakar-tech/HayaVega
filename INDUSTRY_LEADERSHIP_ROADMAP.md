# Mangoo industry-leadership roadmap

## Status: in progress (August 2026) -- Pillar A (Signals) delivered; Pillar B (Performance, phases 1-3 plus the ORM/runtime follow-ups) delivered with published results; Pillar C (Developer experience) delivered in full

This is the next chapter after `UPCOMING_IMPROVEMENT_ROADMAP.md`, whose own
scope ("convert the prototype into a safer, more deployable framework") is
now mostly delivered. This document is more ambitious and longer-range: it
lays out how Mangoo becomes competitive on performance with FastAPI, Django,
and Next.js, ships an inbuilt Signals system, and holds a measurably higher
bar for developer experience.

**Every claim here is falsifiable on purpose.** "Industry leader" is judged
by published, reproducible benchmark numbers and things a reader can run
themselves -- never by adjectives. Where a claim can't yet be measured (no
benchmark suite exists today), this document says so plainly instead of
asserting it. This matches how the rest of this repo is documented: every
delivered item elsewhere links to a test; the landing page was deliberately
rewritten to be "a real, honest project reference" rather than a marketing
page. Nothing below changes that standard.

## The three pillars

| Pillar | Goal | Judged by |
|---|---|---|
| Signals | An inbuilt, named event system on par with what Django/Rails developers expect | `mangoo.addons.signals` shipped, documented, tested |
| Performance | Competitive with or faster than FastAPI, Django, and Next.js on defined workloads | A published, reproducible `benchmarks/` suite with real numbers, including losses |
| Developer experience | A measurably higher bar than the frameworks above | Typed public API (PEP 561), proactive diagnostics, documented error-message standard |

## Pillar A: Signals

**Finding (verified this session):** Mangoo already has exactly the
capability "signals" usually means -- it just ships under the name `Pulse`.
`src/mangoo/addons/pulse/` is a complete, tested, Rust-accelerated in-process
event bus (`PulseBus`, `PulseEvent`), wired into request lifecycle,
authentication, admin actions, and migrations. There's forensic evidence it
was originally prototyped under the name `signals` before ever being
committed: `src/mangoo/addons/signals/__pycache__/` contains compiled
bytecode for an `__init__.py` and `contracts.py` that don't exist as source
anywhere and have no git history, while `tests/test_signals_addon.py` still
carries that original filename despite importing from `mangoo.addons.pulse`
today.

**Decision (confirmed with the user):** rebrand rather than rebuild. No new
event system is needed -- Pulse's design (deterministic ordering, error
isolation, sensitive-key rejection, always-async delivery, Rust dispatcher
with a Python fallback) is sound and stays as-is. Only the name changes to
the more industry-familiar term.

### Deliverables

- [x] Rename the package: `mangoo.addons.pulse` -> `mangoo.addons.signals`. `PulseBus` -> `SignalBus`, `PulseEvent` -> `SignalEvent`, `PulseDispatcher` (Rust) -> `SignalDispatcher`.
- [x] Keep `Pulse*` names as deprecated aliases (`PulseBus = SignalBus` -- identity-equal, not a copy -- with a `DeprecationWarning` on first use) rather than a hard break, consistent with this project's "never break the public API without a documented migration" practice elsewhere (e.g. the `mangoo`/`mango`/`mangoo-admin` CLI aliases).
- [x] Fix `tests/test_signals_addon.py` to import from `mangoo.addons.signals`; delete the orphaned `src/mangoo/addons/signals/__pycache__/` bytecode once real source lives at that path.
- [x] Rename `src/mangoo/addons/pulse/README.md` -> `src/mangoo/addons/signals/README.md`; update every reference in `README.md`, `UPCOMING_IMPROVEMENT_ROADMAP.md`, and `PROJECT_MINDMAP.md`, and the event names emitted by `runtime.py`/`auth_api.py`/`admin.py`/`migrations.py` (`request.*`, `auth.*`, `admin.action`, `migration.applied` stayed the same *event names* -- only the Python import path and class names changed).
- [x] Update `create_application`, `auth_routes`, `admin_routes`, `migrations.apply`'s `pulse=` keyword argument to `signals=` (with `pulse=` kept as a deprecated alias, deferring to `signals=` when both are given).

Delivered Aug 21, 2026: see Week 12 in `UPCOMING_IMPROVEMENT_ROADMAP.md` for the full deliverable/acceptance breakdown and test references.

### Acceptance criteria

- [x] Every existing Pulse test passes against the new import path with only mechanical changes (import path and class name, no logic changes) -- see the renamed `tests/test_signals_integration.py`.
- [x] Code still using `from mangoo.addons.pulse import PulseBus` continues to work, with a single `DeprecationWarning`, until the alias is removed in a documented future release -- proven for the import itself and for all four `pulse=` call sites in `tests/test_signals_deprecation.py`.
- [x] No orphaned bytecode or stale filenames remain (`git status` clean; `find src/mangoo/addons -name "*.pyc"` matches only real source).

### Stretch goal (not scheduled)

Model-lifecycle signals -- `pre_save`/`post_save`/`pre_delete`/`post_delete`
decorators tied directly to `Entity`/`Collection` CRUD (`append`/`revise`/
`erase`), the Django-familiar pattern -- were considered and explicitly not
chosen for this round. Recorded here so the option isn't lost: it would
build on top of the renamed `SignalBus`, not replace it, and is a
reasonable next step once Signals itself has shipped and been used for a
while.

## Pillar B: Performance leadership

### Where Mangoo stands today (verified this session, not assumed)

- **No benchmark suite exists.** `find . -iname "*benchmark*"` returns
  nothing. No claim of being "faster than X" is currently falsifiable.
- **Three Rust functions are already built but wired to nothing.**
  `crates/mangoo-python/src/lib.rs` defines `serialize_json`,
  `compile_selection`, and `normalize_path` as native PyO3 functions. A
  grep across `src/mangoo/*.py` confirms none of the three are called
  anywhere. Only `MangoList` and the Pulse/Signals dispatcher are actually
  used. This is real, already-paid-for capacity sitting idle.
- **The runtime is dev-only by its own admission.** `src/mangoo/runtime.py`'s
  module docstring reads "Temporary Python development runtime"; `serve()`
  uses `wsgiref.simple_server`, which is single-threaded. There is no
  documentation anywhere in this repo for running Mangoo behind a real
  production server (gunicorn, uWSGI, or an ASGI server) -- a more basic
  gap than any Rust work, and one that costs nothing to close.
- **`UPCOMING_IMPROVEMENT_ROADMAP.md` already deferred this exact planning.**
  "Further Rust migrations" and "replace the WSGI dev server with an
  async/Rust runtime" are listed there as deliberately unscoped, each
  needing "its own dedicated planning and explicit sign-off before
  starting." This section is that planning.

### Phase 1 -- Benchmark infrastructure (do this before anything else)

No performance work below is meaningful without a way to measure it, and no
"faster than FastAPI/Django/Next.js" claim is credible without a
reproducible, published methodology.

- [x] Add a `benchmarks/` directory with fixed, documented scenarios: a
      minimal JSON echo endpoint, a DB-backed list read through each
      framework's own idiomatic ORM/DB access, a template-rendered HTML
      page, and static file serving -- one app per framework (Mangoo,
      FastAPI+Uvicorn, Django). GraphQL and Next.js are explicitly out of
      scope for this pass (see `benchmarks/README.md` for why); a
      Mangoo-only GraphQL endpoint exists in `benchmarks/mangoo_app/app.py`
      but isn't part of the published cross-framework table.
- [x] Fix the load-testing tool (`oha`, a Rust-based generator -- chosen to
      reuse this repo's existing Rust toolchain rather than introduce a new
      one, and to avoid a Python-based generator becoming its own
      bottleneck against a fast JSON-echo endpoint), run parameters
      (10s duration, 30 concurrent connections, a 2s warm-up request before
      each target's measured run), and report p50/p90/p99 latency and
      throughput, not just an average.
- [x] Publish results as they actually come out, including where Mangoo
      currently loses: `benchmarks/results/RESULTS.md`. Mangoo leads on
      `json_echo`, `db_list`, and `static_file`; on `template_render`,
      FastAPI is marginally ahead of Mangoo (612 vs. 561 req/s in the
      published run) -- disclosed, not smoothed over. Re-run and republish
      after each subsequent phase below, so the numbers stay honest over
      time rather than a one-time snapshot.

Delivered Aug 26, 2026. Two real methodology bugs were found and fixed
*during* this work, both documented in `benchmarks/README.md` in full so
the next person (or the next framework added) doesn't rediscover them the
hard way: (1) capturing a benchmarked server's stdout via a `PIPE` that
nothing reads causes the server to block once log output fills the OS pipe
buffer under real load -- every request then times out, which looks
exactly like "the server is too slow" -- fixed by redirecting to a file
instead; (2) Django's `JsonResponse`/`HttpResponse` don't set
`Content-Length` themselves (relying on the WSGI server to infer it, which
`waitress` -- required on Windows since `gunicorn` cannot run there at all
-- does not do), silently disabling HTTP keep-alive and exhausting
Windows' ephemeral port range within seconds under sustained load; fixed
by setting `Content-Length` explicitly in the benchmark's Django views,
disclosed as a config-parity fix rather than quietly tuning it away.

### Phase 2 -- Quick, high-leverage wins

- [x] Document production deployment behind gunicorn (WSGI, multi-worker)
      and/or uWSGI in `DEPLOYMENT.md`; verify `create_application()`'s
      return value actually works as a standard WSGI callable under a real
      multi-worker server (it should -- WSGI is WSGI -- but this has never
      been tested against one in this repo, only against `TestClient` and
      `wsgiref`). **Delivered:** `DEPLOYMENT.md` gained a "Running the
      application" section (gunicorn for Linux/macOS, waitress for
      Windows/any platform, with worker/thread sizing guidance); `mango newproject` now scaffolds `project/wsgi.py` exposing a standard
      `application` object (`src/mangoo/scaffold.py`); verified end-to-end
      against a real `waitress` server on both a freshly scaffolded project
      and `example_site` -- `create_application()`'s return value is a
      genuine, unmodified WSGI callable under a real multi-worker server,
      not just an assumption.
- [x] Benchmark each of the three inert native functions
      (`serialize_json`, `compile_selection`, `normalize_path`) against its
      current pure-Python/`msgspec` equivalent *before* wiring any of them
      in. `msgspec.json.encode` (already used throughout the framework) is
      a mature, heavily optimized library -- the hand-rolled native
      `serialize_json` may not actually beat it. Only wire in what a
      benchmark proves is a real win, per the principle
      `UPCOMING_IMPROVEMENT_ROADMAP.md` already states elsewhere:
      "Benchmark each candidate first; schedule only once a measured win
      justifies it." **Delivered, decision: none get wired in.**
      `benchmarks/native_functions.py` measured all three, twice, for
      reproducibility: Python beat native Rust in every case --
      `serialize_json` lost to `msgspec.json.encode` by ~21.5-22.4x (a
      mature C extension beats a hand-rolled Rust string encoder), and
      `compile_selection`/`normalize_path` lost by ~1.4-2.85x because the
      PyO3 FFI boundary crossing costs more than the few microseconds of
      string formatting it would save. Full numbers and reasoning in
      [`benchmarks/README.md`](benchmarks/README.md#native-function-benchmarks-why-the-three-inert-rust-functions-stay-unused).
      The three functions remain defined in `mangoo_native` but unused --
      correctly, since wiring any of them in would have been a
      regression.

### Phase 3 -- The bigger swing

- [x] ASGI support: async views, deployable via the existing mature
      Uvicorn/Hypercorn ecosystem. **Delivered, with explicit sign-off on
      scope obtained first** (additive only; both sync and async views;
      benchmarked before calling it done -- see below).

  **Scope, as signed off:** `mangoo.asgi.create_asgi_application()` is a
  second, additive transport alongside `mangoo.runtime.create_application()`
  -- the WSGI path is completely unchanged and remains the default; nothing
  about it required touching `runtime.py`. Views may be `async def`
  (awaited directly on the event loop) or plain synchronous functions (run
  via `asyncio.to_thread`, so every existing sync view works unmodified
  under either transport, with zero migration required). `mango newproject` now scaffolds `project/asgi.py` alongside `project/wsgi.py`,
  documented as opt-in in both the generated file's docstring and
  `DEPLOYMENT.md`.

  **A real interoperability gap found and fixed along the way:** `api_route`,
  `csrf_protect`, and the `permission_required` family of decorators
  (`login_required`, `superuser_required`, `email_verified_required`) all
  called `view(request)` synchronously at the end of their wrapper function.
  Wrapping an `async def` view with any of them silently produced an
  unawaited coroutine instead of a `Response` -- caught by a test
  (`test_login_required_composes_with_an_async_view`,
  `test_csrf_protect_composes_with_an_async_view` in `tests/test_asgi.py`)
  written specifically to exercise this composition, not by accident. Fixed
  by having each wrapper check `inspect.iscoroutinefunction(view)` and
  produce a matching coroutine-function wrapper when the underlying view is
  async, so the ASGI runtime awaits it directly instead of routing it to a
  thread pool. `rest.py`, `security.py`.

  **Test client:** `mangoo.testing.AsyncTestClient` (exported from
  `mangoo`, alongside the existing `TestClient`) drives a real ASGI 3.0
  `scope`/`receive`/`send` cycle in-process, synchronously from the
  caller's perspective (one `asyncio.run()` per call) -- matching this
  project's `unittest`-based test style rather than requiring a pytest
  async plugin. 21 tests in `tests/test_asgi.py` plus 2 in
  `tests/test_scaffold.py` (23 total) cover sync/async views, 404/405,
  hardening (host allowlist, oversized-body rejection without reading the
  body), Signals delivery, metrics recording, the deprecated `pulse=`
  alias, ASGI lifespan events, and the decorator-composition fix above.
  Verified end-to-end against a real `uvicorn` server (not just the
  in-memory test harness) serving a freshly scaffolded project.

  **Benchmarked before calling it done, per this document's own
  principle:** `benchmarks/mangoo_asgi_app/` reuses the original Mangoo
  benchmark app's routes and seeded data verbatim, served over
  `mangoo.asgi` + `uvicorn` instead of the WSGI path's `waitress`, so it
  can be compared against FastAPI on the *same server* -- a fairer
  comparison than the original WSGI-vs-uvicorn table, which differs by
  server as well as framework. Two runs at `--duration 10s --connections
  30`: Mangoo (ASGI) beat FastAPI on `json_echo` (1.41x-1.49x),
  `db_list` (1.20x-1.27x), and `static_file` (1.69x-1.72x), and lost
  `template_render` by ~1.14x both times -- the same scenario and the same
  reason (FastAPI's Jinja2 vs. Mangoo's own template engine) the WSGI
  target already lost it by. Full numbers, methodology, and an explicit
  note on what this comparison does *not* yet establish (neither app's
  benchmark exercises actual concurrent I/O, which is the real reason to
  choose ASGI) in
  [`benchmarks/README.md`](benchmarks/README.md#asgi-vs-fastapi).

- [ ] A custom async/Rust HTTP runtime (already flagged as unscoped in
      `UPCOMING_IMPROVEMENT_ROADMAP.md`) remains the largest possible swing
      and is not scheduled here -- it gets its own dedicated planning
      document once Phases 1-2 and ASGI support have produced real
      benchmark data showing where the remaining bottleneck actually is.
      Guessing at a rewrite before that data exists risks solving the
      wrong problem. ASGI support (above) did not surface a bottleneck
      that would justify one.

### Follow-up: chasing the two weak spots ASGI's benchmark surfaced

ASGI support's own benchmark (above) found two weak spots: Mangoo loses
`template_render` to FastAPI's Jinja2 on both transports, and ASGI itself
costs 21-40% throughput on cheap endpoints versus WSGI. Two Rust
candidates and one architecture question were benchmarked before touching
any of the three, per this document's own principle.

- [x] Benchmark a native Rust template-rendering candidate and native
      Rust ASGI header decode/encode, following up on the earlier
      "Python wins all three" native-function result above.
      **Delivered, split result:**
  - **ASGI header decode/encode: Python wins again, consistently with the
    earlier finding.** `decode_request_headers` lost by 2.5x-2.7x,
    `encode_response_headers` by 1.4x-1.6x -- the same FFI-crossing-costs-
    more-than-the-work-saves pattern as `compile_selection`/
    `normalize_path`. **Not wired in.**
  - **Template rendering: Rust wins by 16.6x-20.5x, but this is a
    scoping signal, not a shippable function.** `render_post_list_page`
    is a hand-compiled Rust function for exactly one template's shape; it
    proves a *general* Rust template compiler has a large enough ceiling
    to be worth scoping as its own project, but wiring in this one
    hardcoded function would silently drop support for `{% if %}`,
    `{% extends %}`/`{% block %}`, and arbitrary variable paths --
    a correctness regression, not a performance win. **Not wired in
    either; the roadmap item below is what this benchmark actually
    justifies.**
  - Full numbers and reasoning:
    [`benchmarks/README.md`](benchmarks/README.md#a-second-native-function-benchmark-targeting-the-two-weak-spots-above----one-real-win-one-more-loss).
- [x] Design and benchmark a *general* native Rust template compiler for
      `mangoo.templates`'s actual template language (`{{ }}`,
      `{% if/elif/else %}`, `{% for %}`) -- not the one-template stand-in
      above. **Delivered, and it confirms the exact risk this item was
      scoped around: the 16-20x ceiling did not survive generalization.**
  - Built for real: `crates/mangoo-core` gained `TemplateExpr`/`TemplateNode`
    enums and an interpreter matching `templates.py`'s `_evaluate`/
    `_render_nodes` semantics node-for-node (undefined-vs-null, `loop.*`
    metadata, `and`/`or`/`not`/`==`/`!=`/`in`), with 13 Rust unit tests
    including one reproducing `page.html`'s output byte-for-byte.
    `{% extends %}`/`{% block %}` are not implemented (a template using
    them falls back to Python) -- scoped out deliberately, not an oversight.
  - `benchmarks/native_template_engine.py` benchmarked it against real
    `render_template()` on two templates (the benchmark suite's
    `page.html`, and a synthetic if/elif/else-heavier one). **Python won
    both, by 1.4x-2.1x, across three runs.** A second comparison
    (`CompiledNativeTemplate`, which decodes the node tree once instead
    of on every render) proved this is not an FFI-marshaling artifact:
    caching the decode changed nothing. The loss is the interpretation
    itself -- CPython's own interpreter loop, plus its cheaper native
    dict/attribute lookups, beats a Rust re-implementation that has to
    re-derive the same generality through a more general value type
    (`JsonValue`) and a full context clone on every loop iteration.
  - **Decision: no general Rust template compiler gets built.** The
    earlier hardcoded-function win was never evidence a general compiler
    would win -- it was evidence a zero-branching string builder beats a
    *general interpreter*, which was true before Rust entered the
    picture. If `template_render`'s FastAPI gap is worth closing later,
    this data points at optimizing the existing *Python* interpreter
    (e.g., its per-iteration `dict(context)` copy in `_render_for`) as
    the more promising angle, not porting it to Rust. Full numbers and
    reasoning:
    [`benchmarks/README.md`](benchmarks/README.md#does-a-general-native-template-engine-survive-generalization-no).
- [x] Checked whether Mangoo's existing htmx-attribute hypermedia runtime
      (`mangoo.ui`'s `mangoo-ui.js`, delivered in Week 9/11 of
      `UPCOMING_IMPROVEMENT_ROADMAP.md`) could mask the `template_render`
      slowdown for a realistic partial-update interaction, via a new
      Mangoo-only `benchmarks/hypermedia_fragment.py` scenario comparing a
      full page load against just the fragment an `hx-get` would fetch.
      **Result: no measurable server-throughput difference (0.99x-1.02x
      across two runs)** -- for this template, the 20-row loop dominates
      render cost identically on both routes, so trimming the page
      wrapper trims almost none of the actual work. htmx's real benefit
      for this workload is network payload size and avoided client-side
      work, neither of which a server-throughput benchmark measures --
      documented plainly in
      [`benchmarks/README.md`](benchmarks/README.md#hypermedia-fragment-vs-full-page-what-htmx-actually-saves-here)
      rather than left as an assumption.
- [x] Verified Mangoo's htmx capability is genuinely wired up on a real
      page, not just exercised in isolated unit tests. Added
      `benchmarks/mangoo_app`'s `/posts/live` page: a real `Refresh`
      button with `hx-get`/`hx-target`/`hx-swap` loading the actual
      `mangoo-ui.js` runtime via `ui_routes()` -- kept as a separate
      route/template from `posts_page`/`page.html` so the published
      cross-framework `template_render` numbers above are never affected.
      `benchmarks/verify_htmx_wiring.py` checks the full server-side
      contract the client-side JS depends on (since this environment has
      no browser/DOM to execute the JS itself): real `hx-*` attributes
      pointing at a real endpoint, the fragment endpoint's output matching
      byte-for-byte what's already in the swap target, and the served
      `mangoo-ui.js` containing the genuine `hx-get`/`hx-target`/`hx-swap`
      handling code rather than a stub. All three checks pass. Details in
      [`benchmarks/README.md`](benchmarks/README.md#proving-the-htmx-wiring-is-real-not-just-documented).

  **Update Aug 27-28, 2026: `mangoo.ui` was removed entirely, then rebuilt
  the next day, narrower in scope (Stage 1 hypermedia only, no client-side
  signals layer) with a leaner JS runtime and a benchmarked Rust option
  for fragment rendering.** The removal was a product-direction decision,
  not a finding from this benchmark (client-side JS runs in the browser
  and is invisible to `oha`'s request timing either way). The two items
  above describe the original implementation and stay accurate as
  historical record; the current `benchmarks/hypermedia_fragment.py` and
  `benchmarks/verify_htmx_wiring.py` are the rebuilt versions -- see
  `UPCOMING_IMPROVEMENT_ROADMAP.md`'s "Week 14: revert the UI framework"
  and "Week 15: rebuild the UI framework" for the full record of both
  changes, including the new Rust fragment-rendering benchmark (a real
  4x-8x isolated win and a real 1.05x-1.08x HTTP-level win -- the first
  Rust candidate all session to show *any* measurable HTTP-level effect,
  unlike the compiled template engine's zero-visible-effect result above).

- [x] Followed the Python-side lead the template-compiler item above
      pointed at: `_render_for`'s per-iteration `dict(context)` copy.
      **Delivered, a real but modest win, after one dead end.** A
      `collections.ChainMap`-based attempt (avoid the copy by layering a
      small overrides dict) was tried first and measured **slower**
      (1.15x-1.20x) -- its per-key lookup overhead is paid on every
      `{{ }}`/condition inside the loop body, not just once per iteration,
      which outweighed the copy it avoided. Reverted. What shipped
      instead: mutate `context` in place for the loop variable and `loop`
      metadata, restoring the previous values once after the whole loop
      finishes, safe because that `context` is always a private dict
      `render_template` creates for its own render, never the caller's
      object. **Result: 1.02x-1.17x faster and up to 37% less peak memory**
      (wider contexts benefit more, since the old cost was
      `O(items x len(context))` and the new cost is `O(items)`); the full
      cross-framework `template_render` scenario moved from ~494-498 to
      ~512 req/s, narrowing the FastAPI gap from ~10.5% to ~7.6% without
      closing it. All 32 existing template tests pass unchanged. Full
      numbers for both the shipped fix and the reverted ChainMap attempt:
      [`benchmarks/README.md`](benchmarks/README.md#fixing-template_render-in-python-a-real-modest-win).

### Closing the remaining `template_render` gap: both planned items delivered, HTTP gap unchanged -- and why

Both items below were built, benchmarked, and shipped as planned. Neither
moved `template_render`'s HTTP-level numbers, and investigating why
surfaced the actual bottleneck -- a more useful outcome than either item
succeeding at the original goal would have been alone.

- [x] **Quick win: precomputed dotted-path splits.** `_Path` now stores a
      pre-split `parts` tuple at parse time instead of calling
      `dotted.split(".")` on every render. **Delivered: 1.07x-1.18x faster**
      across three same-run comparisons (`benchmarks/template_path_fix.py`).
      Shipped, no behavior change, 32/32 template tests passing.
- [x] **The bigger swing: compile templates to real Python functions.**
      `templates.py` gained a `compile()`/`exec()`-based render path:
      node-level structure (text/if/elif/else/for) compiles to real Python
      control flow from the trusted, already-parsed node tree, while
      expression evaluation still calls the existing `_evaluate()`
      interpreter (deliberately not compiled to raw operators -- its
      undefined-vs-`None` and `in`-operator semantics were correctly judged
      too risky to reimplement for unbenchmarked additional gain).
      `{% extends %}`/`{% block %}` templates automatically and
      transparently fall back to the unchanged interpreter. **Delivered:
      1.27x-2.19x faster** on both the real `page.html` and a synthetic
      if/elif/else-heavier template (`benchmarks/compiled_template_engine.py`,
      three runs). The module docstring was updated exactly as planned, to
      state the precise guarantee ("no template ever executes anything a
      caller supplies") rather than the looser "there is no `eval`" that
      the new internal mechanism would otherwise have appeared to
      contradict. Shipped with dedicated tests proving the compiled path
      is genuinely used for plain templates and genuinely bypassed for
      inheritance ones (`CompiledRenderPathTests`), plus one test fixing a
      real coverage gap the change itself created: the interpreter's
      `_render_for` became reachable only via inheritance templates, and
      none of the existing ones used a `{% for %}` loop.
- **Acceptance result: the `template_render` HTTP benchmark did not move**
  (still ~578-590 req/s on Mangoo's WSGI path) despite both isolated
  improvements being real and reproducible. **Root cause, found by
  investigating rather than assuming the fixes "didn't work":**
  `posts_page`'s view opens a brand-new `sqlite3.connect()` on every
  request (`mangoo.db.connect()`, no pooling) -- and checking
  `benchmarks/fastapi_app/app.py` confirmed FastAPI's `fetch_posts()` does
  the exact same thing, so this ~40-50ms-dominating cost is paid equally
  by both frameworks. Template rendering is only tens to low-hundreds of
  *microseconds* against that -- even a 2x improvement there is too small
  a fraction of total request time to register at the HTTP level. The
  isolated benchmarks are real; the HTTP benchmark simply isn't sensitive
  enough to see them, because it's measuring something else almost
  entirely. Full writeup:
  [`benchmarks/README.md`](benchmarks/README.md#two-more-python-side-wins-shipped----and-why-neither-moves-the-http-benchmark).
- **New, not yet scoped:** the ~20-25% `template_render` gap to FastAPI
  likely isn't primarily template-engine speed anymore (both sides now
  compile structure; Mangoo's engine still calls back into `_evaluate()`
  for expressions, which Jinja2 doesn't need to). Two untested hypotheses
  worth a dedicated look before touching `templates.py` again: (a) Jinja2's
  render is simply faster in absolute terms for the remaining,
  not-yet-compiled expression-evaluation slice, or (b) something in the
  shared per-request SQLite path costs Mangoo's stack slightly more than
  FastAPI's for reasons unrelated to templates entirely.

### Chasing hypothesis (b): a real ORM bug found, a real HTTP-level dead end confirmed

Hypothesis (b) above was investigated directly. It led to a genuine,
previously-undiscovered inefficiency -- fixed -- but not to a closed
`template_render` gap, and the reason why is now understood rather than
assumed.

- [x] **Found and fixed: `Collection.prepare()` ran on every single
      `fetch`/`total`/`revise`/`erase`/`append` call, not once.** Unlike
      `benchmarks/fastapi_app/app.py`'s `fetch_posts()` (one connection, one
      `SELECT`), every one of those five methods called
      `self.collection.prepare()` first, which opened a *second* sqlite3
      connection, ran `CREATE TABLE IF NOT EXISTS`, and committed --
      on every call, forever, long after the table demonstrably already
      existed. `benchmarks/orm_prepare_overhead.py` measured `fetch()`
      against the same FastAPI-shaped raw-`sqlite3` baseline used
      elsewhere in this document: **1.89x slower** before the fix, dropping
      to **1.20-1.25x** after. **Fixed** in `src/mangoo/models.py`:
      `Collection.prepare()` now checks a process-lifetime cache
      (`_PREPARED_TABLES`, keyed by table name and normalized database
      config) before opening a connection at all, since a table's schema
      cannot change without a migration -- a separate, deliberate operation
      this cache has no effect on. Regression coverage:
      `tests/test_models.py::test_repeated_fetches_do_not_reissue_create_table`
      (asserts exactly one connection per `fetch()` once a table is known,
      via a spy on `mangoo.models.connect`) and
      `test_prepare_still_creates_the_table_on_first_use_of_a_new_database`
      (first use against an unseen database still creates the table
      correctly). All 341 tests, `ruff`, and `mypy` stay clean.
- **Acceptance result: the HTTP-level `db_list`/`template_render` numbers
  did not move (three paired before/after runs at the same `--duration
  10s --connections 30`, re-run in both orders to rule out time-of-day
  drift).** This is the same shape of result Phase 3's Python-side template
  wins already hit, and investigating *why* rather than assuming the fix
  "didn't work" found the same kind of answer: a follow-up
  in-process measurement (`TestClient`, no network, no real WSGI server)
  timed Mangoo's entire `posts_page` view -- ORM fetch, template render,
  routing, and hardening together -- at **~1.0ms**, while the same
  scenario's real HTTP p50 under `waitress`/`oha` is **~45-55ms**, a
  ~45-55x gap between "the app's own code" and "what the benchmark
  measures." A saving of well under 1ms (what removing the redundant
  connection recovers) is proportionally invisible against a ~45ms
  request. **Conclusion: the remaining `template_render` (and most of
  `db_list`'s) gap to FastAPI is not attributable to any code this project
  controls in `mangoo.templates` or `mangoo.models` -- both are now
  confirmed fast in absolute terms -- but to something in the
  server/concurrency/SQLite-file-locking layer beneath the application,
  common to both frameworks' benchmark harnesses.** That is a different,
  larger investigation (thread-pool sizing under `waitress`/`uvicorn`,
  SQLite behavior under concurrent access from many threads to one file,
  possibly OS/filesystem-level factors) than anything `templates.py` or
  `models.py` can fix, and is not scoped here.
- **The `Collection.prepare()` fix ships regardless of the HTTP-benchmark
  result above**, on its own merits: it is strictly less work for
  identical behavior (a schema that isn't there yet still gets created
  correctly; a schema that's already there stops being redundantly
  re-checked), verified safe against every existing test including the
  MySQL/PostgreSQL integration suites (which manage their own tables
  directly via `migrations.apply`/raw SQL, never through
  `Collection.prepare()`, so the new process-lifetime cache never
  observes a table it must forget). A synthetic loopback benchmark on one
  machine not showing a saving is not evidence the saving is unreal --
  fewer connections and fewer redundant statements against a shared
  SQLite file is the kind of fix that matters more, not less, under real
  concurrent production load than under this benchmark's specific
  bottleneck.

### Finding the environment ceiling, and a real (if modest) WSGI-specific fix on top of it

The user asked, directly: both transports should be faster than FastAPI on
every scenario, and Mangoo's ORM should be benchmarked against other ORMs
(Tortoise named specifically). Before chasing either, the `db_list`/
`template_render` mystery above needed a control this document hadn't run
yet: is the ~45ms floor specific to Mangoo, or does *any* Python web app
hit it under this exact concurrency on this machine?

- [x] **Control experiment: a completely minimal, framework-free WSGI app**
      doing the identical `sqlite3.connect()` + `SELECT` + `close()` per
      request, served by the same `waitress --threads=16`, hit by the same
      `oha -z 10s -c 30`. **Result: ~750-820 req/s, p50 ~34ms** -- within
      noise of Mangoo (589-784), FastAPI (705-752), and Django (322-387)
      alike. **This proves the ~700-800 req/s ceiling on this workload is
      an environment property (this machine's SQLite-under-concurrent-
      connections-and-waitress-threads behavior on Windows), not something
      any of the three frameworks' code can currently be blamed for or
      beat through code changes alone.** "Beat FastAPI on every scenario"
      was never really the achievable target here -- reaching the shared
      ceiling was, and Mangoo's ASGI transport already does (784 vs
      FastAPI's 705-752).
- [x] **Given that, where WSGI specifically still had room: a CPU profile
      (`cProfile`, matching the real benchmark app's `create_application()`
      call with no `hardening=` argument, exactly as `benchmarks/
      mangoo_app/app.py` configures it) of the full request path found two
      real, avoidable per-request costs in `runtime.py`'s `application()`
      closure**, both shared verbatim by `asgi.py`'s `application()`:
  - A 14-entry `status_text` dict was constructed fresh on **every single
    request**, regardless of route, to look up one reason phrase. Hoisted
    to a module-level `_STATUS_TEXT` constant.
  - When no `hardening=` is configured (the benchmark app's own
    configuration, and any project that hasn't opted into hardening yet),
    a whole new `Response` dataclass instance was constructed on every
    successful request for the sole purpose of appending one
    `X-Request-ID` header tuple -- immediately superseded a few lines
    later by the final WSGI/ASGI header list construction. Replaced with
    an `extra_headers` tuple merged directly into that final header list,
    removing the redundant allocation entirely rather than just making it
    cheaper.
  - **Fixed** in `src/mangoo/runtime.py` and `src/mangoo/asgi.py`
    identically. All 348 tests, `ruff`, and `mypy` stay clean.
- **Measured effect: real, but modest, exactly as the earlier finding
  predicts.** `db_list` (Mangoo WSGI): 589 req/s before, 618-639 across
  two isolated re-runs after, 637 in the full published re-run -- roughly
  +8%. `cProfile` shows why it isn't larger: `sqlite3.connect()` +
  `.execute()` alone account for ~48% of total CPU time in this path (the
  ORM's per-request row hydration into typed `Entity` objects is the next
  largest cost, ~18%) -- ordinary Python dict/dataclass overhead was
  always going to be a small slice next to that, and the fix's actual
  value is proportional to its share of that total, not to how "obviously
  wasteful" the code looked in isolation.
- **Not attempted this round, and why:** removing the ORM's per-request
  row hydration, or pooling SQLite connections instead of opening one per
  request, would address a larger share of the remaining cost -- but both
  are real behavior/architecture changes (typed `Entity` objects are the
  ORM's whole value proposition; connection pooling changes lifecycle and
  thread-safety guarantees used by every part of the framework that calls
  `mangoo.db.connect()`, not just this benchmark's view) that need their
  own scoping and sign-off, not a change folded into a profiling pass.
- **Tortoise ORM as a new comparison target was explicitly deferred, not
  forgotten.** It uses an async driver (`aiosqlite`), a different shape of
  comparison than anything `benchmarks/` currently runs (every existing
  target opens a plain synchronous `sqlite3` connection per request,
  Mangoo's ORM included) -- building and validating that comparison
  fairly is new scope, requested but not yet started.

### Following through on ORM row hydration: `Selection.values()`, and it closed most of the remaining gap

The item flagged above as "not attempted this round, needs its own
scoping" got that scoping directly: the user asked again for
`template_render` speed specifically. Row hydration was the identified,
addressable cost, and the fix chosen is additive (a new method, not a
change to `fetch()`'s existing behavior or return type), so it carries
none of the risk a change to `fetch()` itself or to `mangoo.db.connect()`
would.

- [x] **Added `Selection.values(*fields)`** to `src/mangoo/models.py`:
      returns plain `dict` rows straight from the database, skipping
      `Entity` construction and every column's `Column.coerce()` entirely.
      Pass specific column names to select only those (as
      `benchmarks/mangoo_app/app.py` now does), or none for every column.
      Composes with `seek`/`arrange`/`window` exactly like `fetch()`
      does. `fetch()` itself is completely unchanged -- this is a new,
      additive option for the common case (list/JSON rendering) that
      never needed `Entity` behavior in the first place, not a
      replacement.
- [x] **Benchmarked in isolation before wiring in anywhere, per this
      document's own standing principle:** `fetch()` + manual dict
      extraction (what `benchmarks/mangoo_app/app.py` did before) took
      0.636-0.657ms per call for 20 rows; `values("title", "views")` took
      0.512-0.517ms -- a real **1.28-1.36x** win, consistent with the
      profiling that pointed here (~18% of total per-request CPU time was
      `Entity` hydration).
- [x] **Wired into `benchmarks/mangoo_app/app.py`'s `posts_json` and
      `posts_page`** (the exact views the published `db_list`/
      `template_render` numbers measure) -- `posts_json` now returns
      `.values("id", "title", "views")` directly (no manual list
      comprehension needed, since `values()` already returns dicts) and
      `posts_page` passes `.values("title", "views")` straight to
      `render_template()`. Reused verbatim by `benchmarks/mangoo_asgi_app`
      via its existing `from benchmarks.mangoo_app.app import urlpatterns`,
      so both transports picked up the fix from one change. 4 new tests in
      `tests/test_models.py` cover column selection, selecting every
      column, an unknown-column rejection, and composition with
      `seek`/`arrange`/`window`. All 352 tests, `ruff`, and `mypy` stay
      clean.
- **Measured effect (two full paired benchmark runs, `--duration 10s
  --connections 30`): real and substantial, not the "proportionally
  invisible" result earlier fixes in this document hit.**
  - `db_list`: Mangoo WSGI 637 -> **694, then 727** req/s (FastAPI: 705-727)
    -- **Mangoo WSGI now ties FastAPI outright** (727 vs 727 in the second
    run), up from an ~10% deficit before this fix. Mangoo (ASGI): 784 ->
    **813, then 864** req/s, extending its existing lead.
  - `template_render`: Mangoo WSGI 605 -> **671, then 681** req/s (FastAPI:
    752-766) -- gap narrowed from ~20% to ~11%, not closed outright on
    WSGI. **Mangoo (ASGI) 674 -> 760, then 808 req/s -- now beats FastAPI
    on this scenario too** (808 vs 766), the one scenario it previously
    lost. **Mangoo (ASGI) now leads FastAPI on all four published
    scenarios.**
- **Why this fix moved the needle when the `Collection.prepare()` fix
  (which saved a comparable or larger absolute amount of time) barely
  did:** `values()`'s saving lands specifically in CPU-bound Python
  object-construction work (20 `Entity.__init__`/`__set__`/`coerce()`
  calls per request) rather than I/O-adjacent work like an extra
  connection. Under the GIL, saved CPU time subtracts directly from the
  ceiling on concurrent throughput; saved wall-clock time spent on
  something a raw single-request measurement doesn't reproduce (I/O
  scheduling, thread handoff) does not, as intensively as this document's
  earlier findings already showed. This is consistent with, not a
  contradiction of, the "environment ceiling" finding above -- `db_list`
  and `template_render` still top out well short of `json_echo`'s
  throughput, because the SQLite connection and query itself remain
  unavoidable, unreduced costs common to every framework in this suite.
- **What's left, honestly:** `template_render` on WSGI still trails
  FastAPI by ~11%. The remaining, still-unaddressed costs are the same
  ones already named above -- `sqlite3.connect()`/`.execute()` (now the
  dominant remaining cost by a wider margin, since hydration is no longer
  competing with it) and the per-request connection-per-request pattern
  itself. Closing that gap fully would mean connection pooling or reuse,
  which changes lifecycle/thread-safety guarantees for every caller of
  `mangoo.db.connect()`, not only this benchmark's views -- still real,
  still deliberately not attempted without its own dedicated scoping.

### Line-by-line memory profiling: tooling now in place for the above

Before starting the two items above, the project needed a working way to
see *which lines* allocate memory, not just aggregate before/after totals.
**Delivered:** three tools were tried, tested against `templates.py`'s
real render path, and two turned out to have genuine limitations for this
specific workload (many small, short-lived allocations per render) that
were discovered by testing, not assumed:

- [x] **Scalene** (combined CPU+memory, per-line, and the only option that
      can see inside `mangoo_native`'s Rust code) -- **fails to build on
      this repo's Windows dev environment** (`pip install scalene` needs
      Microsoft C++ Build Tools, not present). A real Scalene-on-Windows
      gap, documented the same way this document already documents
      `gunicorn`'s Windows limitation. Available via WSL/Linux/macOS for
      anyone who wants that deeper view later.
- [x] **`memory_profiler`** installs cleanly on Windows, but samples
      process RSS -- for a tight render loop, CPython's own refcounting
      frees garbage before the next sample, so it correctly shows ~0 MiB
      per line. Right tool for confirming no leak/unbounded growth (used
      that way in `benchmarks/memory_profile_templates.py`: 20,000 renders,
      0.1 MiB total growth, noise-level); wrong tool for "which line
      allocates the most."
- [x] **`tracemalloc`** (stdlib) tracks real allocator activity, but a
      plain before/after snapshot diff has the same blind spot for a
      different reason -- refcounting frees most of a single render's
      garbage before the "after" snapshot. What already worked this
      session (`template_context_fix.py`'s approach): sample
      `get_traced_memory()`'s **peak** during a batch of calls while
      tracing stays active, catching each call's live-at-peak footprint.
      This is the tool to keep using for the compile-to-function work
      above, not a new one to build.

Full writeup, including the exact failed attempts and why:
[`benchmarks/README.md`](benchmarks/README.md#line-by-line-memory-profiling-what-actually-works-on-this-dev-environment).

## Pillar C: Developer-experience leadership

- [x] Linting and formatting: `ruff check`/`ruff format` as a required CI
      job, configured in `pyproject.toml`. Delivered Aug 21, 2026 -- see
      "Order 8a" / Week 10 in `UPCOMING_IMPROVEMENT_ROADMAP.md`.
- [x] Add a `py.typed` marker (PEP 561) to `src/mangoo/` so type checkers
      recognize the package's existing type hints, and bring those hints
      under CI enforcement with `mypy`. **Delivered:** `src/mangoo/py.typed`
      confirmed present in a real built wheel; `[tool.mypy]` added to
      `pyproject.toml` (default strictness, narrow overrides only for the
      optional `mangoo_native` extension and optional `psycopg`/`pymysql`
      drivers); a separate required `typecheck` CI job runs `mypy` on every
      push and PR; 44 real type errors found and fixed across 12 files
      (dynamic metaclass attributes on `Entity`, several untyped-empty-
      collection locals, one genuine null-safety gap in
      `management/commands.py`) rather than suppressed. Same work as
      "Order 8b" in `UPCOMING_IMPROVEMENT_ROADMAP.md`, see Week 13 there
      for the full writeup.
- [x] Add a `manage.py check` / `mango check` diagnostics command
      (Django-style): proactively reports unsafe settings, deprecated API
      usage (including the Pulse-to-Signals alias above), and pending
      migrations, before the developer hits them at runtime. **Delivered
      as `diagnose`, not `check`** -- Django's own command is literally
      called `check`, and this project had just finished renaming every
      other command that collided with Django's naming (`runserver` ->
      `launch`, `startproject` -> `newproject`, etc., all in
      `management/commands.py`'s `_DEPRECATED_ALIASES`); adding a brand
      new command that reintroduced the exact collision that work just
      removed would have been inconsistent with its own stated purpose.
      `src/mangoo/checks.py`'s `run_checks(settings)` returns
      `CheckMessage` findings (level, message, actionable hint), most
      severe first; wired into both `manage.py diagnose` and `mango
      diagnose`. Checks three things a developer would otherwise only
      discover at runtime: unsafe settings (`DEBUG` left on, a placeholder
      `SECRET_KEY`, a wildcard or empty `ALLOWED_HOSTS`, `FORCE_HTTPS`/
      `SECURE_COOKIES` off -- severity scaled by whether `DEBUG` is on,
      matching `validate_production`'s existing judgment about what's
      merely worth a warning in development versus a hard error in
      production); deprecated Pulse-era API usage (a source-text scan for
      `mangoo.addons.pulse`, `PulseBus`/`PulseEvent`/`PulseSubscriber`,
      and `pulse=`, across the project's own `.py` files, skipping
      `migrations/`/`.venv`/`.git`/etc.); and pending migrations (reusing
      `migrations.status()`, the same function `migrationstatus` already
      calls). 17 tests in `tests/test_checks.py`, plus CLI-level coverage
      in `tests/test_management.py` and `tests/test_cli.py`. Verified
      against the real scaffolded sample project, not just unit tests:
      catches a real `DEBUG`/`SECRET_KEY` pair and a real injected
      `from mangoo.addons.pulse import PulseBus` line, each with the
      exact file it was found in.
- [x] Write down an explicit error-message quality bar and apply it
      everywhere: file-and-line context plus an actionable next step, the
      standard `TemplateSyntaxError`, `GraphQLSyntaxError`, and the admin's
      form validation already meet -- extend it to routing 404s, settings
      validation, and migration errors, which don't consistently meet it
      today. **The standard, written down:** every error a developer (not
      an anonymous HTTP client -- see the security fix below for that
      boundary) can encounter should name *where* the problem is (a file,
      a module, or as close to a line number as the error's own nature
      allows) and *what to do next* in the same message, not just what
      went wrong. Applied to the three named gaps:
  - **Routing 404s** now name the requested path and, only when
    `debug=True`, list every registered route pattern -- the same
    tradeoff Django's own `DEBUG` 404 page makes (listing an app's own
    route table is standard development-mode behavior, not the kind of
    confidential system/credential detail the error-disclosure fix below
    is careful to never expose regardless of `debug`). Computed once at
    application-build time, not per request, so it costs nothing when
    `debug=False` (the default) or when a route *does* match.
    `src/mangoo/runtime.py`, `src/mangoo/asgi.py`.
  - **Settings validation** (`conf.py`'s `load_settings` and
    `production.py`'s `validate_production`) now names the exact settings
    module (`ProjectSettings.module_name`, new field) in every message,
    e.g. "Set SECRET_KEY to a unique, environment-provided value in
    project.settings" instead of just "SECRET_KEY must be a unique
    environment-provided value" -- a real settings module has no
    meaningful "line number" to point at (these are runtime attribute
    checks against an already-imported module, not a parse error), so the
    module name is the honest equivalent of "file" here.
  - **Migration errors** (`migrations.py`'s `apply()`) now include the
    migration's source file path alongside its name, and each message
    ends with a concrete instruction ("Fix the operation in
    `<path>` and re-run", "revert `<path>` to its applied contents, or
    write a new migration instead"). These are CLI/operator-facing
    errors (raised by `manage.py applymigrations`, seen only by whoever
    runs that command), so including the underlying database error's
    message is appropriate and expected here -- a materially different
    trust boundary than an HTTP response to an anonymous client, which is
    exactly the distinction the error-disclosure fix below draws.
  - Regression coverage: `tests/test_runtime.py`,
    `tests/test_asgi.py` (`debug_404_names_the_requested_path_...`/
    `production_404_never_lists_registered_routes` pairs, proving both
    the improvement and its production-mode boundary), `tests/
    test_conf.py`, `tests/test_production.py`, `tests/test_migrations.py`.
- [x] **Found and fixed along the way, not originally scoped here: a real
      confidential-data disclosure gap**, per direct user request to audit
      every error path (API, code, connection, or otherwise) for
      leaking system/database details. `graphql.py`'s resolver-exception
      handler put a caught exception's raw message directly into the
      client-facing GraphQL response, unconditionally -- a database
      driver's connection-failure message routinely embeds the host,
      port, and username it tried, which would have gone straight to any
      GraphQL client. **Fixed:** `Schema(debug=False)` (new, default)
      exposes only `error_type` (e.g. `"RuntimeError"`) to the client; the
      full exception, with traceback, is always logged server-side.
      `runtime.py`/`asgi.py`'s generic 500 handler was also inconsistent
      (always HTML, even for `api_route`-marked JSON endpoints) --
      unified into the documented `{"detail": string}` envelope with an
      added `error_type` for API routes, plain HTML with `error_type` for
      page routes, never the exception's message either way. One existing
      test (`test_runtime_hides_traceback_and_logs_server_error`) had been
      written to hide *even the exception type name* -- a stricter stance
      than requested; updated it to the new, explicitly-requested
      behavior rather than silently overriding a prior deliberate design
      choice without saying so. Tracked in
      `SECURITY_REVIEW_CHECKLIST.md`'s "Error disclosure" row with direct
      test references.

## Sequencing

~~Signals first (cheap, mechanical, no design risk)~~ -- delivered.
~~Benchmark infrastructure (nothing after this point is credible without
it)~~ -- delivered, see Phase 1 above.
~~Phase 2: gunicorn/waitress deployment docs + inert-native-function
benchmarks~~ -- delivered, decision was "don't wire the native functions
in" (see Phase 2 above).
~~Phase 3: ASGI support~~ -- delivered, with scope sign-off obtained first
and benchmarked against FastAPI on the same server before being called done
(see Phase 3 above).
~~Pillar C: `diagnose` diagnostics command, the error-message quality
bar (routing 404s, settings validation, migration errors), and the
error-disclosure security fix found along the way~~ -- delivered, see
Pillar C above.
Remaining, deliberately unscheduled: a custom async/Rust HTTP runtime --
ASGI support's benchmark data did not surface a bottleneck that would
justify planning it; connection pooling for the last `template_render`
gap on WSGI -- a real architecture change touching every caller of
`mangoo.db.connect()`, not something to fold into a quick fix; a Tortoise
ORM benchmark comparison -- new scope (an async-driver comparison app),
requested but not started; dependency scanning and release automation
(`UPCOMING_IMPROVEMENT_ROADMAP.md`'s Order 8c).

## Definition of done

Matching the standard already set in `UPCOMING_IMPROVEMENT_ROADMAP.md`, an
item here is complete only when:

1. It is documented in the relevant guide (`README.md`, `DEPLOYMENT.md`, or
   `BUILD_FROM_SCRATCH.md`).
2. It has unit and regression tests, or -- for performance work
   specifically -- a published, reproducible benchmark result.
3. The full test suite passes.
4. No prior claim in this document or elsewhere becomes false as a result
   (e.g., a Pulse rename doesn't silently break existing applications).
