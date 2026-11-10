# Hayavega industry-leadership roadmap

## Status: in progress (August 2026) -- Pillar A (Signals) delivered; Pillar B (Performance, phases 1-3 plus the ORM/runtime follow-ups) delivered with published results; Pillar C (Developer experience) delivered in full

This is the next chapter after `UPCOMING_IMPROVEMENT_ROADMAP.md`, whose own
scope ("convert the prototype into a safer, more deployable framework") is
now mostly delivered. This document is more ambitious and longer-range: it
lays out how Hayavega becomes competitive on performance with FastAPI, Django,
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
| Signals | An inbuilt, named event system on par with what Django/Rails developers expect | `hayavega.addons.signals` shipped, documented, tested |
| Performance | Competitive with or faster than FastAPI, Django, and Next.js on defined workloads | A published, reproducible `benchmarks/` suite with real numbers, including losses |
| Developer experience | A measurably higher bar than the frameworks above | Typed public API (PEP 561), proactive diagnostics, documented error-message standard |

## Pillar A: Signals

**Finding (verified this session):** Hayavega already has exactly the
capability "signals" usually means -- it just ships under the name `Pulse`.
`src/hayavega/addons/pulse/` is a complete, tested, Rust-accelerated in-process
event bus (`PulseBus`, `PulseEvent`), wired into request lifecycle,
authentication, admin actions, and migrations. There's forensic evidence it
was originally prototyped under the name `signals` before ever being
committed: `src/hayavega/addons/signals/__pycache__/` contains compiled
bytecode for an `__init__.py` and `contracts.py` that don't exist as source
anywhere and have no git history, while `tests/test_signals_addon.py` still
carries that original filename despite importing from `hayavega.addons.pulse`
today.

**Decision (confirmed with the user):** rebrand rather than rebuild. No new
event system is needed -- Pulse's design (deterministic ordering, error
isolation, sensitive-key rejection, always-async delivery, Rust dispatcher
with a Python fallback) is sound and stays as-is. Only the name changes to
the more industry-familiar term.

### Deliverables

- [x] Rename the package: `hayavega.addons.pulse` -> `hayavega.addons.signals`. `PulseBus` -> `SignalBus`, `PulseEvent` -> `SignalEvent`, `PulseDispatcher` (Rust) -> `SignalDispatcher`.
- [x] Keep `Pulse*` names as deprecated aliases (`PulseBus = SignalBus` -- identity-equal, not a copy -- with a `DeprecationWarning` on first use) rather than a hard break, consistent with this project's "never break the public API without a documented migration" practice elsewhere (e.g. the `hayavega`/`hayavega`/`hayavega-admin` CLI aliases).
- [x] Fix `tests/test_signals_addon.py` to import from `hayavega.addons.signals`; delete the orphaned `src/hayavega/addons/signals/__pycache__/` bytecode once real source lives at that path.
- [x] Rename `src/hayavega/addons/pulse/README.md` -> `src/hayavega/addons/signals/README.md`; update every reference in `README.md`, `UPCOMING_IMPROVEMENT_ROADMAP.md`, and `PROJECT_MINDMAP.md`, and the event names emitted by `runtime.py`/`auth_api.py`/`admin.py`/`migrations.py` (`request.*`, `auth.*`, `admin.action`, `migration.applied` stayed the same *event names* -- only the Python import path and class names changed).
- [x] Update `create_application`, `auth_routes`, `admin_routes`, `migrations.apply`'s `pulse=` keyword argument to `signals=` (with `pulse=` kept as a deprecated alias, deferring to `signals=` when both are given).

Delivered Aug 21, 2026: see Week 12 in `UPCOMING_IMPROVEMENT_ROADMAP.md` for the full deliverable/acceptance breakdown and test references.

### Acceptance criteria

- [x] Every existing Pulse test passes against the new import path with only mechanical changes (import path and class name, no logic changes) -- see the renamed `tests/test_signals_integration.py`.
- [x] Code still using `from hayavega.addons.pulse import PulseBus` continues to work, with a single `DeprecationWarning`, until the alias is removed in a documented future release -- proven for the import itself and for all four `pulse=` call sites in `tests/test_signals_deprecation.py`.
- [x] No orphaned bytecode or stale filenames remain (`git status` clean; `find src/hayavega/addons -name "*.pyc"` matches only real source).

### Stretch goal (not scheduled)

Model-lifecycle signals -- `pre_save`/`post_save`/`pre_delete`/`post_delete`
decorators tied directly to `Entity`/`Collection` CRUD (`append`/`revise`/
`erase`), the Django-familiar pattern -- were considered and explicitly not
chosen for this round. Recorded here so the option isn't lost: it would
build on top of the renamed `SignalBus`, not replace it, and is a
reasonable next step once Signals itself has shipped and been used for a
while.

## Pillar B: Performance leadership

### Where Hayavega stands today (verified this session, not assumed)

- **No benchmark suite exists.** `find . -iname "*benchmark*"` returns
  nothing. No claim of being "faster than X" is currently falsifiable.
- **Three Rust functions are already built but wired to nothing.**
  `crates/hayavega-python/src/lib.rs` defines `serialize_json`,
  `compile_selection`, and `normalize_path` as native PyO3 functions. A
  grep across `src/hayavega/*.py` confirms none of the three are called
  anywhere. Only `MangoList` and the Pulse/Signals dispatcher are actually
  used. This is real, already-paid-for capacity sitting idle.
- **The runtime is dev-only by its own admission.** `src/hayavega/runtime.py`'s
  module docstring reads "Temporary Python development runtime"; `serve()`
  uses `wsgiref.simple_server`, which is single-threaded. There is no
  documentation anywhere in this repo for running Hayavega behind a real
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
      page, and static file serving -- one app per framework (Hayavega,
      FastAPI+Uvicorn, Django). GraphQL and Next.js are explicitly out of
      scope for this pass (see `benchmarks/README.md` for why); a
      Hayavega-only GraphQL endpoint exists in `benchmarks/hayavega_app/app.py`
      but isn't part of the published cross-framework table.
- [x] Fix the load-testing tool (`oha`, a Rust-based generator -- chosen to
      reuse this repo's existing Rust toolchain rather than introduce a new
      one, and to avoid a Python-based generator becoming its own
      bottleneck against a fast JSON-echo endpoint), run parameters
      (10s duration, 30 concurrent connections, a 2s warm-up request before
      each target's measured run), and report p50/p90/p99 latency and
      throughput, not just an average.
- [x] Publish results as they actually come out, including where Hayavega
      currently loses: `benchmarks/results/RESULTS.md`. Hayavega leads on
      `json_echo`, `db_list`, and `static_file`; on `template_render`,
      FastAPI is marginally ahead of Hayavega (612 vs. 561 req/s in the
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
      Windows/any platform, with worker/thread sizing guidance); `hayavega newproject` now scaffolds `project/wsgi.py` exposing a standard
      `application` object (`src/hayavega/scaffold.py`); verified end-to-end
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
      The three functions remain defined in `hayavega_native` but unused --
      correctly, since wiring any of them in would have been a
      regression.

### Phase 3 -- The bigger swing

- [x] ASGI support: async views, deployable via the existing mature
      Uvicorn/Hypercorn ecosystem. **Delivered, with explicit sign-off on
      scope obtained first** (additive only; both sync and async views;
      benchmarked before calling it done -- see below).

  **Scope, as signed off:** `hayavega.asgi.create_asgi_application()` is a
  second, additive transport alongside `hayavega.runtime.create_application()`
  -- the WSGI path is completely unchanged and remains the default; nothing
  about it required touching `runtime.py`. Views may be `async def`
  (awaited directly on the event loop) or plain synchronous functions (run
  via `asyncio.to_thread`, so every existing sync view works unmodified
  under either transport, with zero migration required). `hayavega newproject` now scaffolds `project/asgi.py` alongside `project/wsgi.py`,
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

  **Test client:** `hayavega.testing.AsyncTestClient` (exported from
  `hayavega`, alongside the existing `TestClient`) drives a real ASGI 3.0
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
  principle:** `benchmarks/hayavega_asgi_app/` reuses the original Hayavega
  benchmark app's routes and seeded data verbatim, served over
  `hayavega.asgi` + `uvicorn` instead of the WSGI path's `waitress`, so it
  can be compared against FastAPI on the *same server* -- a fairer
  comparison than the original WSGI-vs-uvicorn table, which differs by
  server as well as framework. Two runs at `--duration 10s --connections
  30`: Hayavega (ASGI) beat FastAPI on `json_echo` (1.41x-1.49x),
  `db_list` (1.20x-1.27x), and `static_file` (1.69x-1.72x), and lost
  `template_render` by ~1.14x both times -- the same scenario and the same
  reason (FastAPI's Jinja2 vs. Hayavega's own template engine) the WSGI
  target already lost it by. Full numbers, methodology, and an explicit
  note on what this comparison does *not* yet establish (neither app's
  benchmark exercises actual concurrent I/O, which is the real reason to
  choose ASGI) in
  [`benchmarks/README.md`](benchmarks/README.md#asgi-vs-fastapi).

- [x] ~~A custom async/Rust HTTP runtime (already flagged as unscoped in
      `UPCOMING_IMPROVEMENT_ROADMAP.md`) remains the largest possible swing
      and is not scheduled here~~ -- originally left unscheduled pending
      benchmark data showing where the remaining bottleneck actually was
      (ASGI support did not surface one). Since delivered on direct
      request, as a synchronous thread-pool runtime rather than an async
      `tokio`/`hyper` one, with the scope decision and two real bugs found
      while benchmarking it disclosed in "The custom Rust HTTP runtime:
      delivered" below, and measured directly against FastAPI in "The Rust
      runtime vs. FastAPI, directly measured" below.

### Follow-up: chasing the two weak spots ASGI's benchmark surfaced

ASGI support's own benchmark (above) found two weak spots: Hayavega loses
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
      `hayavega.templates`'s actual template language (`{{ }}`,
      `{% if/elif/else %}`, `{% for %}`) -- not the one-template stand-in
      above. **Delivered, and it confirms the exact risk this item was
      scoped around: the 16-20x ceiling did not survive generalization.**
  - Built for real: `crates/hayavega-core` gained `TemplateExpr`/`TemplateNode`
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
- [x] Checked whether Hayavega's existing htmx-attribute hypermedia runtime
      (`hayavega.ui`'s `hayavega-ui.js`, delivered in Week 9/11 of
      `UPCOMING_IMPROVEMENT_ROADMAP.md`) could mask the `template_render`
      slowdown for a realistic partial-update interaction, via a new
      Hayavega-only `benchmarks/hypermedia_fragment.py` scenario comparing a
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
- [x] Verified Hayavega's htmx capability is genuinely wired up on a real
      page, not just exercised in isolated unit tests. Added
      `benchmarks/hayavega_app`'s `/posts/live` page: a real `Refresh`
      button with `hx-get`/`hx-target`/`hx-swap` loading the actual
      `hayavega-ui.js` runtime via `ui_routes()` -- kept as a separate
      route/template from `posts_page`/`page.html` so the published
      cross-framework `template_render` numbers above are never affected.
      `benchmarks/verify_htmx_wiring.py` checks the full server-side
      contract the client-side JS depends on (since this environment has
      no browser/DOM to execute the JS itself): real `hx-*` attributes
      pointing at a real endpoint, the fragment endpoint's output matching
      byte-for-byte what's already in the swap target, and the served
      `hayavega-ui.js` containing the genuine `hx-get`/`hx-target`/`hx-swap`
      handling code rather than a stub. All three checks pass. Details in
      [`benchmarks/README.md`](benchmarks/README.md#proving-the-htmx-wiring-is-real-not-just-documented).

  **Update Aug 27-28, 2026: `hayavega.ui` was removed entirely, then rebuilt
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
  (still ~578-590 req/s on Hayavega's WSGI path) despite both isolated
  improvements being real and reproducible. **Root cause, found by
  investigating rather than assuming the fixes "didn't work":**
  `posts_page`'s view opens a brand-new `sqlite3.connect()` on every
  request (`hayavega.db.connect()`, no pooling) -- and checking
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
  compile structure; Hayavega's engine still calls back into `_evaluate()`
  for expressions, which Jinja2 doesn't need to). Two untested hypotheses
  worth a dedicated look before touching `templates.py` again: (a) Jinja2's
  render is simply faster in absolute terms for the remaining,
  not-yet-compiled expression-evaluation slice, or (b) something in the
  shared per-request SQLite path costs Hayavega's stack slightly more than
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
      to **1.20-1.25x** after. **Fixed** in `src/hayavega/models.py`:
      `Collection.prepare()` now checks a process-lifetime cache
      (`_PREPARED_TABLES`, keyed by table name and normalized database
      config) before opening a connection at all, since a table's schema
      cannot change without a migration -- a separate, deliberate operation
      this cache has no effect on. Regression coverage:
      `tests/test_models.py::test_repeated_fetches_do_not_reissue_create_table`
      (asserts exactly one connection per `fetch()` once a table is known,
      via a spy on `hayavega.models.connect`) and
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
  timed Hayavega's entire `posts_page` view -- ORM fetch, template render,
  routing, and hardening together -- at **~1.0ms**, while the same
  scenario's real HTTP p50 under `waitress`/`oha` is **~45-55ms**, a
  ~45-55x gap between "the app's own code" and "what the benchmark
  measures." A saving of well under 1ms (what removing the redundant
  connection recovers) is proportionally invisible against a ~45ms
  request. **Conclusion: the remaining `template_render` (and most of
  `db_list`'s) gap to FastAPI is not attributable to any code this project
  controls in `hayavega.templates` or `hayavega.models` -- both are now
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
every scenario, and Hayavega's ORM should be benchmarked against other ORMs
(Tortoise named specifically). Before chasing either, the `db_list`/
`template_render` mystery above needed a control this document hadn't run
yet: is the ~45ms floor specific to Hayavega, or does *any* Python web app
hit it under this exact concurrency on this machine?

- [x] **Control experiment: a completely minimal, framework-free WSGI app**
      doing the identical `sqlite3.connect()` + `SELECT` + `close()` per
      request, served by the same `waitress --threads=16`, hit by the same
      `oha -z 10s -c 30`. **Result: ~750-820 req/s, p50 ~34ms** -- within
      noise of Hayavega (589-784), FastAPI (705-752), and Django (322-387)
      alike. **This proves the ~700-800 req/s ceiling on this workload is
      an environment property (this machine's SQLite-under-concurrent-
      connections-and-waitress-threads behavior on Windows), not something
      any of the three frameworks' code can currently be blamed for or
      beat through code changes alone.** "Beat FastAPI on every scenario"
      was never really the achievable target here -- reaching the shared
      ceiling was, and Hayavega's ASGI transport already does (784 vs
      FastAPI's 705-752).
- [x] **Given that, where WSGI specifically still had room: a CPU profile
      (`cProfile`, matching the real benchmark app's `create_application()`
      call with no `hardening=` argument, exactly as `benchmarks/
      hayavega_app/app.py` configures it) of the full request path found two
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
  - **Fixed** in `src/hayavega/runtime.py` and `src/hayavega/asgi.py`
    identically. All 348 tests, `ruff`, and `mypy` stay clean.
- **Measured effect: real, but modest, exactly as the earlier finding
  predicts.** `db_list` (Hayavega WSGI): 589 req/s before, 618-639 across
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
  `hayavega.db.connect()`, not just this benchmark's view) that need their
  own scoping and sign-off, not a change folded into a profiling pass.
- **Tortoise ORM as a new comparison target was explicitly deferred, not
  forgotten.** It uses an async driver (`aiosqlite`), a different shape of
  comparison than anything `benchmarks/` currently runs (every existing
  target opens a plain synchronous `sqlite3` connection per request,
  Hayavega's ORM included) -- building and validating that comparison
  fairly is new scope, requested but not yet started.

### Following through on ORM row hydration: `Selection.values()`, and it closed most of the remaining gap

The item flagged above as "not attempted this round, needs its own
scoping" got that scoping directly: the user asked again for
`template_render` speed specifically. Row hydration was the identified,
addressable cost, and the fix chosen is additive (a new method, not a
change to `fetch()`'s existing behavior or return type), so it carries
none of the risk a change to `fetch()` itself or to `hayavega.db.connect()`
would.

- [x] **Added `Selection.values(*fields)`** to `src/hayavega/models.py`:
      returns plain `dict` rows straight from the database, skipping
      `Entity` construction and every column's `Column.coerce()` entirely.
      Pass specific column names to select only those (as
      `benchmarks/hayavega_app/app.py` now does), or none for every column.
      Composes with `seek`/`arrange`/`window` exactly like `fetch()`
      does. `fetch()` itself is completely unchanged -- this is a new,
      additive option for the common case (list/JSON rendering) that
      never needed `Entity` behavior in the first place, not a
      replacement.
- [x] **Benchmarked in isolation before wiring in anywhere, per this
      document's own standing principle:** `fetch()` + manual dict
      extraction (what `benchmarks/hayavega_app/app.py` did before) took
      0.636-0.657ms per call for 20 rows; `values("title", "views")` took
      0.512-0.517ms -- a real **1.28-1.36x** win, consistent with the
      profiling that pointed here (~18% of total per-request CPU time was
      `Entity` hydration).
- [x] **Wired into `benchmarks/hayavega_app/app.py`'s `posts_json` and
      `posts_page`** (the exact views the published `db_list`/
      `template_render` numbers measure) -- `posts_json` now returns
      `.values("id", "title", "views")` directly (no manual list
      comprehension needed, since `values()` already returns dicts) and
      `posts_page` passes `.values("title", "views")` straight to
      `render_template()`. Reused verbatim by `benchmarks/hayavega_asgi_app`
      via its existing `from benchmarks.hayavega_app.app import urlpatterns`,
      so both transports picked up the fix from one change. 4 new tests in
      `tests/test_models.py` cover column selection, selecting every
      column, an unknown-column rejection, and composition with
      `seek`/`arrange`/`window`. All 352 tests, `ruff`, and `mypy` stay
      clean.
- **Measured effect (two full paired benchmark runs, `--duration 10s
  --connections 30`): real and substantial, not the "proportionally
  invisible" result earlier fixes in this document hit.**
  - `db_list`: Hayavega WSGI 637 -> **694, then 727** req/s (FastAPI: 705-727)
    -- **Hayavega WSGI now ties FastAPI outright** (727 vs 727 in the second
    run), up from an ~10% deficit before this fix. Hayavega (ASGI): 784 ->
    **813, then 864** req/s, extending its existing lead.
  - `template_render`: Hayavega WSGI 605 -> **671, then 681** req/s (FastAPI:
    752-766) -- gap narrowed from ~20% to ~11%, not closed outright on
    WSGI. **Hayavega (ASGI) 674 -> 760, then 808 req/s -- now beats FastAPI
    on this scenario too** (808 vs 766), the one scenario it previously
    lost. **Hayavega (ASGI) now leads FastAPI on all four published
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
  `hayavega.db.connect()`, not only this benchmark's views -- still real,
  still deliberately not attempted without its own dedicated scoping.

### Connection pooling, attempted: a real win for ASGI, a real regression for WSGI

The item above got its scoping and sign-off. Delivered as designed, and
the result is not the clean win the roadmap item implied it might be --
disclosed here exactly as measured, both the win and the loss.

- [x] **`hayavega.db.enable_connection_pooling()`** (new, off by default):
      reuses one connection per (thread, database config) instead of
      opening a new one on every `connect()` call. Deliberately scoped to
      match this framework's existing concurrency model -- one connection
      per worker thread, not a general bounded pool with checkout/checkin
      queuing across threads -- so it needed no change to any existing
      caller of `connect()` (`models.py`, `auth.py`, `admin.py`,
      `migrations.py`, `production.py`'s readiness check all benefit
      automatically): only what `connect()` and `Connection.close()` do
      internally changed, not their contracts. A pooled connection's
      `close()` calls `rollback()` instead of actually closing -- not a
      no-op -- so a connection that outlives a single call never carries
      an exception's half-finished transaction into whichever request
      reuses it next, regardless of whether the prior caller committed,
      only read, or failed mid-write. `pool_connections=True` on
      `create_application`/`create_asgi_application` is a convenience that
      calls the same function (documented as process-global, not scoped to
      one application instance, since that's what it actually is). 9 new
      tests in `tests/test_db.py`, including one that specifically proves
      the rollback-on-close correctness property (an uncommitted write
      through a pooled connection is verified gone after `close()`, not
      silently persisted) and one proving pooling is thread-local (a
      second thread gets its own separate connection, never the first
      thread's). All 389 tests, `ruff`, and `mypy` stay clean.
- [x] **Verified the mechanism actually engages under a real server, not
      just `TestClient`:** instrumented `connect()` under a real `waitress`
      instance (4 threads, 10 requests) -- confirmed exactly 4 distinct
      connections, one per thread, each thread consistently reusing its
      own connection across every request it handled.
- **Measured effect on `hayavega.asgi` + uvicorn: a real, substantial win.**
  `db_list`: 727-864 -> 905-937 req/s. `template_render`: 760-808 ->
  840-855 req/s -- widening Hayavega (ASGI)'s lead over FastAPI on both
  scenarios it was already ahead on.
- **Measured effect on the WSGI path (`waitress`): a real, consistent
  ~10-15% *regression*, not a null result.** `db_list` dropped from the
  637-727 req/s range to 589-710 req/s across repeated alternating on/off
  runs at the same settings. This surprised the implementation, so it was
  chased down rather than shipped anyway:
  - First ruled out the correctness fix as the cause: a variant with a
    truly-inert no-op `close()` (no `rollback()` call at all) showed the
    same regression, isolating it to holding a connection open at all, not
    to anything this pool's own bookkeeping does.
  - Reproduced with zero framework code: a standalone script with 16
    Python threads hitting the same SQLite file directly (no Hayavega, no
    WSGI, no HTTP) showed the identical pattern -- 16 threads each holding
    one persistent connection open throughout was measurably slower than
    16 threads each opening a fresh connection per call and closing it
    immediately. **This is a property of many threads holding a
    persistent SQLite connection open simultaneously under contention on
    this environment, not a bug in the pool's bookkeeping** -- consistent
    with, and a further data point for, this document's standing
    "environment ceiling" finding: SQLite's own concurrent-access behavior,
    not application code, is what's actually being measured at this layer.
  - **Decision: wired into `benchmarks/hayavega_asgi_app/app.py` only.**
    `benchmarks/hayavega_app/app.py` (WSGI) leaves `pool_connections`
    unset, with an inline comment stating why, rather than silently
    omitting a feature that measurably regresses the exact scenario the
    roadmap item set out to improve.
  - `enable_connection_pooling()`'s own docstring now states this
    asymmetry plainly and tells a caller to benchmark their own
    deployment rather than assume the ASGI result transfers -- the same
    standard this document holds every other performance claim to.
- **`template_render` on WSGI is unchanged by this work** (still ~11%
  behind FastAPI) -- the one thing this item was chartered to close. Not
  a wasted effort: it produced a real, shippable win for ASGI, ruled out
  a specific, plausible-sounding fix for WSGI with actual evidence rather
  than leaving it an open guess, and surfaced a genuinely new, useful data
  point (many-threads-holding-SQLite-connections-open is itself a cost,
  not just neutral) for whoever picks up the WSGI gap next.

### Read-only SQLite connections for pure reads: a real, shipped isolated win -- HTTP-level result unchanged, and consistent with the pattern above

Connection pooling (above) was one specific idea for the WSGI `template_render`
gap and it regressed WSGI. This is a different, narrower idea for the same
gap, tried next: `Selection.fetch()`/`values()`/`total()` never write, so do
they need a read-write connection at all? SQLite does extra bookkeeping on
every statement over a read-write connection to guard against a concurrent
writer (reserved/pending lock checks) that a connection opened read-only
never needs to do.

- [x] **Added `hayavega.db.connect_readonly()`**: for SQLite, opens
      `file:<path>?mode=ro` (a URI-mode read-only connection) instead of a
      plain read-write one; falls back to `connect()` if the read-only open
      fails (in practice never reached, since every read call site runs
      `Collection.prepare()` first, which always creates the file via a
      normal `connect()` if missing) and for non-SQLite engines (no
      equivalent tested here). Wired into `Selection.fetch()`, `values()`,
      and `total()` only -- `Collection.append()`/`Selection.revise()`/
      `erase()` are unaffected and keep using `connect()`. Unlike
      `enable_connection_pooling()`, a connection opened here is still
      opened and closed fresh on every call, never held open across
      requests -- it does not reproduce the many-threads-holding-a-
      connection-open contention that made pooling regress WSGI, because it
      is a different mechanism (skip lock bookkeeping) rather than the same
      mechanism (reuse the connection) applied more narrowly.
- [x] **Benchmarked in isolation before wiring in anywhere, per this
      document's own standing principle** (`benchmarks/sqlite_readonly_reads.py`):
      through the *exact same* `Selection.values()` code path, with only the
      connection function swapped via monkeypatch (so `prepare()`'s cache
      lookup, `_sql()` building, and dict construction are identical in both
      arms -- the connection is the only variable), for the same 20-row
      query `benchmarks/hayavega_app/app.py` runs. Two full runs, single-
      threaded and under 16-thread contention:

  | Variant | Single-thread (run 1 / run 2) | 16-thread throughput (run 1 / run 2) |
  |---|---:|---:|
  | `values()` with `connect_readonly()` [shipped] | 1.4300 / 1.3986 ms/call | 3006 / 3084 calls/sec |
  | `values()` with plain `connect()` [pre-change] | 1.7080 / 1.8473 ms/call | 2519 / 2532 calls/sec |
  | Raw sqlite3, read-write connect (FastAPI-shaped) | 1.5273 / 1.3995 ms/call | 2834 / 3258 calls/sec |
  | Raw sqlite3, read-only URI connect | 1.3938 / 1.1852 ms/call | 3643 / 3301 calls/sec |

  **Result: a real, reproducible win at the ORM layer** -- `values()` with
  `connect_readonly()` beat the identical code path on plain `connect()` by
  16-24% single-threaded and 19-22% under contention, both runs, matching
  the isolated raw-sqlite3 finding (read-only URI connect beats read-write
  by a comparable margin on this environment) that motivated trying this in
  the first place.
- [x] **Verified against the real test suite, not just the isolated
      benchmark:** `tests/test_models.py::test_repeated_fetches_do_not_reissue_create_table`
      updated to spy on `connect_readonly` (the function `fetch()` now
      actually calls) instead of `connect`, still asserting exactly one
      connection per `fetch()` once a table is prepared. All 389 tests,
      `ruff`, and `mypy` stay clean.
- **Measured at the HTTP level (`waitress`, `oha -z 10s -c 30`, two paired
  before/after runs): no measurable movement, within noise.** `db_list`:
  786-820 req/s before, 807-821 req/s after. `template_render`: 665-666
  req/s before, 635-672 req/s after. Both ranges overlap run-to-run noise
  seen elsewhere in this document at the same settings.
- **Why, and it is the same answer this document already reached for the
  `_render_for`/`Collection.prepare()`/path-splitting fixes above:** the
  saving this change recovers is a fraction of a millisecond of SQLite lock
  bookkeeping inside a request whose *own* `sqlite3.connect()` +
  `.execute()` already costs ~35-45ms end to end on this environment (the
  "environment ceiling" finding earlier in this document). A sub-millisecond
  saving is proportionally invisible against that floor, exactly like the
  `Collection.prepare()` connection-count fix was before it.
- **Ships regardless, on the same standing the `Collection.prepare()` fix
  shipped on:** it is strictly less work for identical behavior on the read
  path (a query that never writes no longer pays for write-lock bookkeeping
  it never uses), it is unconditional (no new opt-in flag, no documented
  asymmetry between transports the way connection pooling has), and the
  isolated ORM-layer benchmark is real even though this specific HTTP
  benchmark isn't sensitive enough to see it -- consistent with this
  document's standard of shipping a proven-real, low-risk improvement on its
  own merits rather than only on whether one specific benchmark moves.
- **The WSGI `template_render` gap to FastAPI (~11%) remains open** as far
  as any single targeted fix goes -- see the re-measurement directly below,
  which found the gap itself is no longer reproducible, for a different
  reason than any one fix closing it.

### Re-measuring the WSGI `template_render` gap: no longer reproducible, and why that's not the same as "fixed"

Asked directly to work the `template_render` gap further, the first step
was re-measuring it before trying anything new -- the last few fixes in
this document (`values()` row hydration, `Collection.prepare()` caching,
the compiled-template-engine work, read-only connections above) had each
shipped on their own isolated merits despite showing "no measurable
HTTP-level movement" individually, so it was worth checking whether they
had quietly added up, or whether the ~11% figure even still held.

- **Four full `oha -z 10s -c 30` runs of the published suite, no code
  changed between them:**

  | Run | Hayavega (WSGI) req/s | FastAPI req/s | Winner (margin) |
  |---|---:|---:|---:|
  | 1 | 721 | 722 | FastAPI (0.1%) |
  | 2 | 736 | 626 | Hayavega (17.6%) |
  | 3 | 670 | 761 | FastAPI (13.6%) |
  | 4 | 751 | 760 | FastAPI (1.2%) |
  | **Average** | **719.5** | **717.25** | **statistical tie (0.3%)** |

- **The historically documented, persistent ~11% FastAPI lead is not
  reproducible today: the average across four fresh runs is a dead heat**,
  with individual runs swinging by as much as 17% in either direction
  around that average -- run-to-run noise on this specific scenario is
  now large enough that a single run (as several earlier snapshots in
  this document were) is not a reliable read on which framework is
  actually ahead.
- **This is disclosed as a re-measurement, not claimed as a new fix.** No
  code changed between these four runs -- this is the *existing* codebase
  (already including every prior fix in this document) measured four times
  in a row. The most likely explanation is the accumulated effect of
  several earlier "no measurable HTTP-level movement" fixes -- each one
  individually too small to see against the ~35-45ms per-request SQLite
  floor, but a handful of them together apparently were enough to erode a
  gap that used to read consistently as ~11% in single-run snapshots.
  Environment differences between sessions (this machine's own load at
  measurement time) cannot be ruled out either, and are the more likely
  explanation for *why the noise itself is this large*, per this
  document's own standing "environment ceiling" finding.
- **Practical conclusion: stop treating single-run `template_render`
  numbers as a reliable signal for this specific scenario.** Any future
  claim about this gap -- in either direction -- needs multiple runs
  averaged, exactly as done here, not the single-run figures this document
  used in earlier sessions. The roadmap item is not "closed" in the sense
  of a fix shipped against it this session; it is closed in the sense that
  the gap it was tracking no longer measurably exists, given today's noise
  level.

### Pooling read-only connections specifically: a real isolated win, no HTTP-level win either -- disclosed, not shipped as default

Asked whether anything could push WSGI faster still -- specifically
whether a Rust template engine could help (already ruled out, see "Does a
general native template engine survive generalization? No." above) -- the
next concrete idea was connection pooling's own regression: it was
attributed to many threads holding a **read-write** connection open under
contention. A read-only connection takes no locks at all, so pooling
*those* specifically was hypothesized not to carry the same cost.

- [x] **Verified the hypothesis in isolation first, per this document's own
      standing principle.** A 16-thread microbenchmark compared a fresh
      read-only connection per call, a pooled read-only connection (one
      per thread, reused), a fresh read-write connection per call, and a
      pooled read-write connection (included as a same-run sanity check,
      to confirm it still reproduces the known regression on this exact
      run). **Result: pooled read-only connections won clearly, by
      ~27-43% over every other option across two runs**, while pooled
      read-write connections landed right where the known regression
      predicted -- no faster than a fresh read-write connection. The
      hypothesis held at this layer.
- [x] **Shipped as `hayavega.db.enable_readonly_connection_pooling()`**,
      independent of `enable_connection_pooling()` -- it only changes
      `connect_readonly()` (and therefore `Selection.fetch()`/`values()`/
      `total()`); the write path is completely unaffected and stays
      governed by the existing flag alone. 5 new tests in `tests/test_db.py`
      (`ReadonlyConnectionPoolingTests`) mirror the existing pooling test
      shape: default-off, same-thread reuse, thread-locality, and that
      enabling one flag never enables the other. All 394 tests, `ruff`,
      and `mypy` stay clean.
- **Measured at the HTTP level before enabling it anywhere -- and it did
  not hold up.** Four paired `oha -z 10s -c 30` runs against
  `benchmarks/hayavega_app/app.py` under real `waitress`, with and without
  this enabled:

  | Scenario | Read-only pooling enabled (4 runs) | Unpooled baseline (4 runs) |
  |---|---:|---:|
  | `db_list` | 826, 916, 762, 739 (avg 811) | 900, 788, 762, 908 (avg 840) |
  | `template_render` | 718, 794, 670, 649 (avg 708) | 836, 723, 677, 705 (avg 735) |

  **Both averages landed slightly *below* the unpooled baseline** (~3.5-4%)
  -- within this scenario's established run-to-run noise band (up to
  ~17%, per the re-measurement above), so this reads as no real
  difference rather than a real regression, but it is clearly not the
  27-43% win the isolated microbenchmark predicted.
- **Why, and it is the same answer every connection-layer optimization in
  this document has reached:** the isolated benchmark measures a
  sub-millisecond connection-open saving in perfect isolation; a real
  request pays that saving against a total cost of ~35-45ms, dominated by
  `sqlite3.connect()`+`.execute()`'s own cost and general WSGI/waitress
  request-handling overhead. A sub-millisecond saving is proportionally
  invisible against that floor -- consistent with the `Collection.prepare()`
  fix, the plain read-only-connection fix, and now this one.
- **Decision: shipped as a real, tested, documented capability -- not
  wired into `benchmarks/hayavega_app/app.py` by default, and not
  recommended as a default for this workload shape.** It is not reverted,
  because the underlying mechanism is correct and the isolated win is
  real; a different deployment (a busier database, a slower filesystem,
  more connection-open overhead relative to total request time than this
  environment has) could plausibly still benefit. The docstring states
  this exact disclosed result and tells the caller to benchmark their own
  deployment, matching the standard every other tunable in this module is
  held to.
- **This closes out the concrete ideas this document had for the WSGI
  connection-layer cost specifically.** Between this, plain read-only
  connections, and read-write pooling, every angle on "hold or skip a
  connection differently" has now been tried and none produced a durable
  HTTP-level win on this environment -- consistent with the "environment
  ceiling" finding that the real cost is `sqlite3.connect()`+`.execute()`
  itself (and general request-handling overhead), not which connection
  strategy wraps it.

### ASGI's own concurrency ceiling: a real, large, reproducible fix -- and a benchmark that didn't hold up, disclosed rather than hidden

Separately from the WSGI `template_render` gap above, a direct request
("ASGI is also needed to be fast in all the scenarios") pointed at a gap in
Hayavega's ASGI transport itself: it was trailing FastAPI (and sometimes
Django) on `json_echo` even though that scenario does no database work at
all -- a pure transport-overhead question, not the SQLite-cost question the
WSGI gap already is.

- [x] **Found: every synchronous view under ASGI was dispatched through
      `asyncio.to_thread`**, which runs on `asyncio`'s event-loop-wide
      *default* executor -- lazily created, sized
      `min(32, os.cpu_count() + 4)`. On this project's 4-core dev machine
      that is 8 worker threads, but `benchmarks/run.py` drives every
      scenario at `oha -c 30` (30 concurrent connections). That sizing is
      tuned for occasional CPU-bound offloading elsewhere in a program, not
      for an entire web application's concurrent I/O-bound sync views, so
      it queues requests well before the database, the template engine, or
      the network becomes the real bottleneck -- a queuing ceiling with
      nothing to do with view code speed.
- [x] **Tried to verify with an isolated microbenchmark first, per this
      document's own standing principle -- and it didn't hold up, disclosed
      rather than discarded.** `benchmarks/asgi_executor_sizing.py`
      simulates 30 concurrent "clients" hammering a trivial sync view
      through the default executor versus a dedicated one. The first run
      looked clean (8 workers ~7900 calls/sec, 16 ~9800, 32 and 64 worse
      than the default) and shaped the initial write-up -- but five total
      repeats disagreed on which worker count won each time. Rather than
      keep the flattering first run's numbers, the script and its
      docstring were corrected to disclose the non-reproducibility plainly:
      this in-process simulation is too noisy on this environment to trust
      as evidence on its own, most likely dominated by thread-creation/
      scheduling noise at this call volume rather than measuring worker-
      count itself.
- [x] **The real evidence: a dedicated, explicitly-sized `ThreadPoolExecutor`**
      (`create_asgi_application(..., max_sync_workers=16)`, default 16 to
      match this project's own documented `waitress --threads=16` guidance)
      replacing `asyncio.to_thread`'s shared default executor, in
      `src/hayavega/asgi.py`. Measured at the HTTP level against a real
      `uvicorn` server under real `oha -z 10s -c 30` load -- two full runs
      before the fix, two full runs after:

  | Scenario | Hayavega (ASGI), before | Hayavega (ASGI), after | FastAPI (same runs) |
  |---|---:|---:|---:|
  | `json_echo` | 955, 884 req/s | 1831, 2450 req/s | 1981, 1895 req/s |
  | `db_list` | 605, 534 req/s | 815, 949 req/s | 600, 643 req/s |
  | `template_render` | 538, 531 req/s | 701, 802 req/s | 743, 606 req/s |
  | `static_file` | 629, 636 req/s | 973, 943 req/s | 538, 655 req/s |

  **Result: large and consistent across both after-runs** -- `json_echo`
  roughly doubled (and moved from a clear FastAPI win to a tie/win);
  `db_list` and `static_file`, already ahead of FastAPI, extended their
  lead further (up to 1.81x); `template_render`, previously Hayavega's one
  loss on this transport, now ties or wins depending on the run. No
  scenario regressed.
- [x] **Verified safe, not just fast:** all 389 tests (including
      `tests/test_asgi.py`'s 23 sync/async-composition tests), `ruff`, and
      `mypy` stay clean. The executor is created once per
      `create_asgi_application()` call and shut down on ASGI
      `lifespan.shutdown` rather than leaked; sync views' behavior
      (return value handling, exception-to-500 conversion, Signals/metrics
      recording) is unchanged -- only which thread pool runs them changed.
- **What this does not change:** none of these four scenarios exercise the
  concurrent I/O (calling another service, an async DB driver) that is the
  actual reason to choose ASGI over WSGI in a real project -- that
  comparison remains unbenchmarked here, as already noted in
  `benchmarks/README.md#asgi-vs-fastapi`. This fix closes a transport-level
  concurrency ceiling that was suppressing *every* ASGI scenario's ceiling,
  independent of that larger, still-open question.

Full numbers, the disclosed noisy microbenchmark, and the shipped fix:
[`benchmarks/README.md`](benchmarks/README.md#asgi-vs-fastapi).

### Line-by-line memory profiling: tooling now in place for the above

Before starting the two items above, the project needed a working way to
see *which lines* allocate memory, not just aggregate before/after totals.
**Delivered:** three tools were tried, tested against `templates.py`'s
real render path, and two turned out to have genuine limitations for this
specific workload (many small, short-lived allocations per render) that
were discovered by testing, not assumed:

- [x] **Scalene** (combined CPU+memory, per-line, and the only option that
      can see inside `hayavega_native`'s Rust code) -- **fails to build on
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
- [x] Add a `py.typed` marker (PEP 561) to `src/hayavega/` so type checkers
      recognize the package's existing type hints, and bring those hints
      under CI enforcement with `mypy`. **Delivered:** `src/hayavega/py.typed`
      confirmed present in a real built wheel; `[tool.mypy]` added to
      `pyproject.toml` (default strictness, narrow overrides only for the
      optional `hayavega_native` extension and optional `psycopg`/`pymysql`
      drivers); a separate required `typecheck` CI job runs `mypy` on every
      push and PR; 44 real type errors found and fixed across 12 files
      (dynamic metaclass attributes on `Entity`, several untyped-empty-
      collection locals, one genuine null-safety gap in
      `management/commands.py`) rather than suppressed. Same work as
      "Order 8b" in `UPCOMING_IMPROVEMENT_ROADMAP.md`, see Week 13 there
      for the full writeup.
- [x] Add a `manage.py check` / `hayavega check` diagnostics command
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
      `src/hayavega/checks.py`'s `run_checks(settings)` returns
      `CheckMessage` findings (level, message, actionable hint), most
      severe first; wired into both `manage.py diagnose` and `hayavega
      diagnose`. Checks three things a developer would otherwise only
      discover at runtime: unsafe settings (`DEBUG` left on, a placeholder
      `SECRET_KEY`, a wildcard or empty `ALLOWED_HOSTS`, `FORCE_HTTPS`/
      `SECURE_COOKIES` off -- severity scaled by whether `DEBUG` is on,
      matching `validate_production`'s existing judgment about what's
      merely worth a warning in development versus a hard error in
      production); deprecated Pulse-era API usage (a source-text scan for
      `hayavega.addons.pulse`, `PulseBus`/`PulseEvent`/`PulseSubscriber`,
      and `pulse=`, across the project's own `.py` files, skipping
      `migrations/`/`.venv`/`.git`/etc.); and pending migrations (reusing
      `migrations.status()`, the same function `migrationstatus` already
      calls). 17 tests in `tests/test_checks.py`, plus CLI-level coverage
      in `tests/test_management.py` and `tests/test_cli.py`. Verified
      against the real scaffolded sample project, not just unit tests:
      catches a real `DEBUG`/`SECRET_KEY` pair and a real injected
      `from hayavega.addons.pulse import PulseBus` line, each with the
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
    `src/hayavega/runtime.py`, `src/hayavega/asgi.py`.
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
~~Connection pooling (`hayavega.db.enable_connection_pooling()`) for the last
`template_render` gap on WSGI~~ -- attempted, delivered as a real, tested,
opt-in capability; result was a genuine win for ASGI and a genuine
regression for WSGI specifically, so the WSGI `template_render` gap itself
remains open (see "Connection pooling, attempted" above for the full,
disclosed result).
~~Read-only SQLite connections for pure reads (`hayavega.db.connect_readonly()`)~~
-- attempted, delivered as a real, tested, unconditional improvement on the
read path; a real isolated ORM-layer win (16-24% faster, both runs), but --
like the `_render_for`/`Collection.prepare()`/path-splitting fixes before
it -- no measurable HTTP-level movement, for the same already-documented
reason (the ~35-45ms per-request SQLite connection/query cost dominates
total request time on this environment). Ships anyway, on its own merits
(see "Read-only SQLite connections for pure reads" above for the full,
disclosed result).
~~ASGI's `asyncio.to_thread` default-executor concurrency ceiling
(`max_sync_workers=` on `create_asgi_application`)~~ -- found in response to
a direct request that ASGI be fast on every scenario, not just some;
delivered as a real, large, reproducible fix (`json_echo` roughly doubled;
every scenario ties or wins against FastAPI across two full before/after
runs). The isolated microbenchmark written to support it did not hold up
under repetition and is disclosed as unreliable rather than cited as
evidence -- the HTTP-level before/after comparison is what actually backs
this one (see "ASGI's own concurrency ceiling" above).
~~The WSGI `template_render` gap to FastAPI~~ -- re-measured (four full
runs, no code changed) rather than chased with another targeted fix; the
historically documented ~11% FastAPI lead is no longer reproducible, averaging
to a statistical tie with run-to-run noise as large as 17% in either
direction (see "Re-measuring the WSGI `template_render` gap" above). Not
claimed as a fix shipped this session -- the accumulated effect of several
earlier "no measurable HTTP-level movement" fixes is the most likely
explanation for why a gap that used to read consistently as ~11% no longer
does.
~~Pooling read-only connections specifically (`hayavega.db.enable_readonly_connection_pooling()`)~~
-- asked directly whether anything could push WSGI faster still after a
Rust template engine was ruled out again; delivered as a real, tested,
documented capability with a real 27-43% isolated win, but four paired
HTTP-level runs found no durable benefit (within noise, if anything
slightly below baseline) -- not wired in as a default, kept available for
deployments with a different cost shape (see "Pooling read-only
connections specifically" above).
~~The Rust runtime vs. FastAPI, directly measured~~ -- the Rust runtime had
only ever been benchmarked against Hayavega's own `waitress` baseline; every
FastAPI comparison on file predated it and used `waitress`, so a FastAPI
win was inferred, never measured. Three full runs
(`benchmarks/rust_runtime_vs_fastapi.py`) confirm it directly: the Rust
runtime beats FastAPI on every scenario by 1.68x-5.09x, including
`template_render` (average 1.74x), the one scenario whose `waitress`-era
FastAPI comparison had become unreliable noise (see "The Rust runtime vs.
FastAPI, directly measured" above).
~~Real concurrent I/O under ASGI~~ -- every scenario benchmarked anywhere
in this document was a plain sync view under every transport, including
Hayavega (ASGI); none exercised the one property ASGI actually has that
WSGI doesn't. `benchmarks/asgi_concurrent_io.py` benchmarks a real
`async def` view against a controlled upstream, three full runs: Hayavega
(ASGI) ties FastAPI on real concurrent I/O (1.03x average), and within
Hayavega itself the `async def` view beats its own blocking twin by 3.30x
average -- the concrete, measured version of `hayavega.asgi`'s own
docstring claim. Building it surfaced three real, disclosed bugs in the
benchmark's own plumbing (httpx not overlapping concurrent requests on
this environment; a connection-per-request client reproducing this
project's own previously-diagnosed Windows port-exhaustion failure mode;
a shared-upstream design contaminating later-run targets with earlier
targets' unclosed connections) -- none in Hayavega's or FastAPI's own code
(see "Real concurrent I/O under ASGI" in `benchmarks/README.md` for the
full account).
~~A Tortoise ORM (or other async-driver) benchmark comparison~~ -- built
on direct request as its own app (`benchmarks/tortoise_app`) and its own
runner (`benchmarks/tortoise_vs_hayavega.py`), scoped to the one scenario an
async driver can plausibly change (`db_list`) with the `aiosqlite`
single-worker-thread-per-connection caveat disclosed up front. Three full
runs found Hayavega beats it on both transports (ASGI 1.23x-1.39x, WSGI
1.05x-1.07x), and even FastAPI's own plain-`sqlite3` baseline is within
noise of Tortoise ORM itself -- see "Tortoise ORM: the async-driver
comparison, built and measured" in `benchmarks/README.md` for the full
account and the exact reason (a documented default connection-pool size
of one, contended by every concurrent request in the benchmark).
Still deferred, not needed now: release automation
(`UPCOMING_IMPROVEMENT_ROADMAP.md`'s Order 8d, dependency scanning itself
is delivered, see Order 8c).

### Tortoise ORM: the async-driver benchmark comparison, delivered

This document's own record above called a Tortoise ORM/async-driver
comparison "new scope, requested but not yet started" twice -- once when
first raised, once when the ASGI concurrent-I/O work above closed out
every other open item. Asked directly to go with it, that scope was
built rather than deferred a third time.

- [x] **`benchmarks/tortoise_app/app.py`**: FastAPI + Tortoise ORM over
      `aiosqlite`, the same `Post` shape and `SEED_COUNT` as every other
      target, exposing only `/posts.json` (the one scenario an async
      driver can plausibly change) plus a DB-free `/echo` for the
      readiness check every app in `benchmarks/` already uses.
- [x] **`benchmarks/tortoise_vs_hayavega.py`** pairs it against three
      targets: FastAPI's own plain-`sqlite3` baseline (Tortoise's own
      framework, without Tortoise), Hayavega (ASGI, sync ORM), and Hayavega
      (WSGI, sync ORM).
- [x] **Disclosed before the first run:** `aiosqlite` is not true async
      I/O -- its own source
      (`_connection_worker_thread` in `aiosqlite/core.py`) shows one
      dedicated background thread per connection running the real,
      blocking `sqlite3` calls, with `await` just parking the coroutine on
      a `Future` that thread resolves. Tortoise's default SQLite
      connection pool is also a single connection, making that one
      thread the sole serialization point for every concurrent request --
      the library's own documented default, kept rather than tuned away,
      since benchmarking a different configuration would answer a
      different question than the one asked.
- [x] **Three full `oha -z 10s -c 30` runs, 100% success every target,
      every run:** Hayavega (ASGI) beat Tortoise ORM 1.23x-1.39x, Hayavega
      (WSGI) 1.05x-1.07x, and FastAPI's own `sqlite3` baseline landed
      within noise of Tortoise ORM itself (0.84x-1.03x) rather than
      clearly ahead -- Tortoise ORM's async driver did not give FastAPI a
      database-bound edge over Hayavega's synchronous ORM at this
      concurrency.
- Full table, all three runs, and the exact explanation (contention on a
  single-connection pool at `-c 30` against Hayavega's full 16-worker pools
  on both transports): [`benchmarks/README.md`](benchmarks/README.md#tortoise-orm-the-async-driver-comparison-built-and-measured).

### The custom Rust HTTP runtime: delivered, with a disclosed scope-down and two real bugs found while benchmarking it

This document's own earlier text called a custom async/Rust HTTP runtime
"the largest possible swing," flagged it in `UPCOMING_IMPROVEMENT_ROADMAP.md`
as "a multi-week foundational rewrite, needs its own dedicated planning and
explicit sign-off before starting," and repeatedly noted ASGI's benchmark
data hadn't surfaced a bottleneck that would justify scoping it. Asked
directly to build it -- in-built to the framework, driven by a settings.py
flag rather than a separate tool -- that planning happened as part of
building it, with the scope decision made explicit before any code was
written rather than discovered partway through.

**Scope decision: synchronous thread pool, not async `tokio`/`hyper`.**
CPython's GIL means only one thread ever executes Python view code at a
time no matter how the socket-accept/HTTP-parsing layer is built. An async
I/O multiplexer would let far more *idle* connections be held cheaply, but
would not let more Python view code run *concurrently* than a bounded
thread pool already does -- the exact same reasoning this document already
used for `max_sync_workers` in `hayavega.asgi`. A synchronous, fixed-size
thread pool (matching `waitress`'s own concurrency model) avoids a large
new dependency surface and the real risk of subtle async/GIL interaction
bugs, for no proven concurrency benefit given the GIL constraint on this
specific workload shape.

- [x] **Built `hayavega_native.serve_wsgi()`**
      (`crates/hayavega-python/src/server.rs`): a hand-written HTTP/1.1
      parser (request line, headers, `Content-Length`-framed body; no
      chunked transfer-encoding in this version, disclosed) and a fixed
      thread pool (`workers=`, default 8) that each call
      `TcpListener::accept()` directly (safe to share across threads
      without an explicit `Mutex`), build a real WSGI `environ` dict, call
      the *unchanged* Python `application(environ, start_response)`
      exactly as `wsgiref`/`waitress` do, and write the response back.
      Rust's role is identical to every other native component in this
      codebase: own a narrow, bounded piece of work around unchanged
      Python code, not replace the framework's own request-handling logic.
- [x] **Wired in as `hayavega.rust_runtime`**, selected by
      `SERVER['ENGINE'] = 'rust'` in a project's settings module (default
      `'python'`, i.e. today's behavior, completely unchanged) -- exactly
      the "settings.py, flag-based" requirement asked for.
      `management/commands.py`'s `_launch()` dispatches to
      `hayavega.rust_runtime.serve()` instead of `hayavega.runtime.serve()`
      when configured, reporting `RustRuntimeUnavailableError` plainly
      (not a silent fallback) if `hayavega_native` was not built for this
      install -- the same "actionable error, not a masked failure"
      standard this document's error-message-quality-bar work already set.
- [x] **Found and fixed two real bugs while benchmarking, not shipped
      blind, per this document's own standing principle of benchmarking
      before calling anything done:**
  1. **No `TCP_NODELAY`.** The first working version lost `db_list` to
     `waitress` by 4x (0.25x ratio) with an 846ms p99 -- implausible for a
     transport-layer difference alone. Root cause: Nagle's algorithm on
     the response write interacting with the peer's delayed-ACK timer,
     the single most common latency pitfall in hand-written TCP servers.
     `stream.set_nodelay(true)` fixed it outright.
  2. **No HTTP keep-alive.** Even after that fix, a second full run (no
     code changed) collapsed to a 6.2% success rate on `db_list` -- an
     outright failure, not a slowdown. This is the *exact same* root
     cause this document already diagnosed for the Django benchmark
     target (see "A real gotcha this suite ran into" in
     `benchmarks/README.md`): opening and closing a new TCP connection
     per request under sustained concurrent load exhausts Windows' small
     default ephemeral-port range within seconds. Fixed by implementing
     real HTTP/1.1 keep-alive: a `BufReader` that lives for the whole
     connection (not recreated per request, which would silently discard
     pipelined bytes read ahead into its buffer), `Connection: keep-alive`
     by default with an honored explicit `Connection: close`, and a
     30-second idle-connection read timeout so one slow or abandoned
     client can't pin a worker thread forever and shrink the effective
     pool.
- [x] **Benchmarked before calling it done, two full runs, per this
      document's own principle** (`benchmarks/rust_runtime_vs_wsgi.py`,
      `oha -z 10s -c 30`, same app/routes/seeded data as the published
      cross-framework suite, only the server differing):

  | Scenario | Rust runtime (2 runs) | waitress (2 runs) | Ratio |
  |---|---:|---:|---:|
  | `json_echo` | 7570, 8350 req/s | 2822, 3607 req/s | 2.68x, 2.32x |
  | `db_list` | 1437, 1503 req/s | 772, 837 req/s | 1.86x, 1.80x |
  | `template_render` | 969, 1156 req/s | 689, 731 req/s | 1.41x, 1.58x |
  | `static_file` | 1510, 1908 req/s | 1047, 1195 req/s | 1.44x, 1.60x |

  **A real, substantial win on every scenario in both runs (1.41x-2.68x),
  with a 100% success rate both times** -- unlike every connection-pooling
  idea tried earlier in this document for the WSGI `template_render` gap,
  this genuinely shows up at the HTTP level. The likely reason: those
  earlier ideas targeted the SQLite connection cost specifically, a small
  slice of total request time; this targets the HTTP request/response
  handling layer itself, which every single request pays regardless of
  what the view does, so it isn't competing against a dominant unrelated
  cost the way a connection-layer change was.
- [x] **Tested for correctness, not just speed -- "with and without" as
      asked.** `tests/test_rust_runtime.py` runs the real framework's
      `create_application()` output through both the existing, trusted
      `TestClient` (the "without") and a real socket connection to the
      Rust server (the "with"), asserting byte-identical status codes and
      response bodies for a DB-backed route, a plain route, a 404, and
      query-string parsing. `tests/test_conf.py` and
      `tests/test_management.py` cover `SERVER['ENGINE']` validation and
      `_launch()`'s dispatch, including the unavailable-native-extension
      error path. Wired into the `native-extension` CI job (the one job
      that actually builds `hayavega_native`). 404 total tests, `ruff`,
      `ruff format`, and `mypy` all stay clean; `cargo test -p hayavega-core`
      (14 tests, untouched by this work) still passes.
- **What this is not, disclosed plainly:** HTTP/2, TLS termination, and
  pipelining beyond simple sequential keep-alive are out of scope for this
  version. This is development/load-testing tooling
  (`hayavega launch`'s `SERVER['ENGINE'] = 'rust'`), not a production-server
  claim -- `DEPLOYMENT.md`'s gunicorn/waitress guidance for real deployment
  is completely unchanged.

Full numbers, both bugs, and the scope-decision reasoning:
[`benchmarks/README.md`](benchmarks/README.md#the-rust-runtime-vs-the-python-runtime-waitress).

### Rust runtime hardening: a dedicated audit found five more real issues, three of them exploitable

A follow-up request ("Rust runtime hardening") to audit the delivered
server against the known failure-mode checklist for a hand-written HTTP
server -- not just re-confirm the happy-path benchmark -- found five real
gaps, three of them genuine security bugs (request smuggling via
`Transfer-Encoding: chunked`, the same desync bug via a malformed
`Content-Length`, and HTTP response splitting via unescaped header
values), plus a Slowloris vulnerability and a worker-pool-shrinking panic
risk. All five are fixed, covered by 6 new adversarial-request tests
(`tests/test_rust_runtime_hardening.py`) that send deliberately malformed
requests over raw sockets and assert safe rejection, and re-benchmarked
afterward to confirm zero performance cost (still 1.37x-2.91x over
`waitress`, 100% success both runs). Full detail, all five issues
explained, and the exact fix for each:
[`benchmarks/README.md`](benchmarks/README.md#hardening-pass-the-known-failure-modes-of-a-hand-written-http-server-found-and-fixed).

### Optional TLS termination: delivered; HTTP/2 stays out of scope, with reasoning

A direct follow-up ("Rust runtime: no TLS/HTTP2 -- go with this") asked
for both. Scoped as two separate questions rather than one, and answered
differently for each, disclosed before writing any code:

- **HTTP/2 stays out of scope.** It is built around multiplexing many
  streams over one connection -- fundamentally incompatible with this
  server's synchronous thread-per-connection design without effectively
  building a second scheduler inside it, reversing the earlier scope
  decision that kept this server simple and well-understood in the first
  place. Its main real-world benefit (parallel loading of many small
  browser-requested resources on a web page) also does not transfer to a
  backend API server the way it does to a browser client.
- **TLS was delivered**, because unlike HTTP/2 it just wraps the same
  existing connection -- no change to the concurrency model at all.
  `rustls` with the `ring` crypto provider (confirmed to build cleanly on
  this Windows environment before committing to it -- the default
  `aws-lc-rs` provider needs a C toolchain this environment may not have).
  `SERVER['TLS_CERT']`/`SERVER['TLS_KEY']` in settings, validated to
  require both together and to reject silently-ignored plaintext under
  `SERVER['ENGINE'] = 'python'`.
- **Required a real architectural change, not just a new parameter:** the
  existing keep-alive implementation split each connection into
  independent read/write handles via `TcpStream::try_clone()`, which does
  not work for TLS (a `rustls` session is sequential, single-owner state
  that cannot be split that way). Refactored around a `Conn` enum
  (`Plain`/`Tls`) both directions of I/O flow through via
  `BufReader::get_mut()` for writes -- incidentally simplifying the
  plain-HTTP path too, not just adding TLS capability alongside it
  unchanged.
- **Benchmarked, two runs:** modest overhead (~2% `json_echo`, ~12%
  `db_list`, ~-1%/noise `template_render`) -- TLS handshake cost is paid
  once per connection and amortized by keep-alive, not paid per request.
  **Disclosed rather than smoothed over: HTTPS success rate was
  99.7-99.97%, not a clean 100%**, both runs -- a small number of
  client-side timeouts plausibly from handshake cost interacting with the
  benchmark tool's own deadline, not a server hang (the correctness test
  suite passed cleanly immediately before and after, and the server kept
  answering requests throughout).
- **Verified with a real TLS handshake against a real self-signed
  certificate**, not just that the code compiles --
  `tests/test_rust_runtime_tls.py` (5 tests, wired into the
  `native-extension` CI job), including that a plain HTTP client hitting
  the HTTPS port fails cleanly without taking the server down.

Full numbers, the architectural change, and the HTTP/2 reasoning in full:
[`benchmarks/README.md`](benchmarks/README.md#optional-tls-termination-real-https-modest-overhead).

### The Rust runtime vs. FastAPI, directly measured

A gap in this document's own record, not a new feature request: "The Rust
runtime vs the Python runtime (waitress)" above only ever compared the
Rust runtime against Hayavega's own `waitress` baseline. Every
Hayavega-vs-FastAPI number published anywhere in this document or
`benchmarks/README.md` predates the Rust runtime and used `waitress` as
Hayavega's server -- so "the Rust runtime beats FastAPI" was an inference
(waitress roughly ties FastAPI; the Rust runtime beats waitress by
1.4x-2.7x; therefore...), never a measurement.

- [x] **`benchmarks/rust_runtime_vs_fastapi.py`** pairs the Rust runtime
      directly against FastAPI/`uvicorn` for the first time: same two
      apps, same four scenarios, same seeded data as every other FastAPI
      comparison in this document -- only the missing pairing.
- [x] **Three full `oha -z 10s -c 30` runs**, per this document's own
      rule (established by the `template_render` re-measurement above)
      that a single run is not a reliable read: the Rust runtime beat
      FastAPI on every scenario, every run, by 1.68x-5.09x --
      `json_echo` ~4.9x, `db_list` ~2.3x, `template_render` ~1.74x,
      `static_file` ~2.8x. 100% success rate, both targets, all three
      runs. `template_render` -- the scenario whose `waitress`-era
      FastAPI comparison had eroded to statistical noise -- carries the
      smallest margin, consistent with the SQLite connection/query cost
      it shares with FastAPI dominating a larger share of its request
      time than the HTTP-layer win the Rust runtime actually delivers.
- **The inference is confirmed, not just assumed.** Full table and
  reasoning: [`benchmarks/README.md`](benchmarks/README.md#the-rust-runtime-vs-fastapi-directly-measured).

### Real concurrent I/O under ASGI: the scenario every comparison above left open

Every scenario benchmarked anywhere in this document -- `json_echo`,
`db_list`, `template_render`, `static_file`, and the Rust-runtime work
above -- is a plain synchronous view under every transport, including
Hayavega (ASGI). None of them exercise the one property ASGI actually has
that WSGI doesn't: an `async def` view awaiting I/O yields the event loop
instead of blocking a whole OS thread for the call's duration.
`benchmarks/README.md`'s "ASGI vs FastAPI" section said this outright:
"`async def` views doing concurrent I/O ... still aren't benchmarked
here." `benchmarks/asgi_concurrent_io.py` is that benchmark.

- [x] **A controlled stand-in upstream** (`benchmarks/upstream_app`, a raw
      ASGI app that sleeps for a fixed delay) plus three routes compared:
      `async def remote_io` on Hayavega (ASGI) and FastAPI, and a blocking
      `remote_io_blocking` twin on Hayavega (ASGI)'s sync-dispatch pool,
      Hayavega (WSGI/`waitress`), and FastAPI's thread pool.
- [x] **Three full `oha -z 10s -c 64` runs, 100% success every target,
      every run:**
  - **Hayavega (ASGI) vs FastAPI, both async: a statistical tie (1.03x
    average).** The question this item exists to answer -- Hayavega's ASGI
    transport handles real concurrent I/O as well as FastAPI's, not just
    the plain-sync scenarios benchmarked everywhere else.
  - **Hayavega (ASGI)'s own async view vs its own blocking twin: 3.30x
    average, same process, same routing code, same server.** The
    concrete, measured version of `hayavega.asgi`'s own module docstring
    claim, previously asserted in prose and now demonstrated.
  - A third comparison (Hayavega WSGI vs FastAPI, both blocking) came out
    0.59x, but is disclosed as **not a fair framework-speed reading**:
    FastAPI's blocking path runs through anyio's default 40-token thread
    pool, Hayavega's two blocking paths both use 16 -- at concurrency above
    every pool size, more headroom wins regardless of either framework's
    real per-request cost.
- **Three real bugs were found and fixed while building this, not shipped
  blind -- none in Hayavega's or FastAPI's own code, all in the benchmark's
  own plumbing:** httpx.AsyncClient was found not to overlap concurrent
  requests at all on this dev environment (a direct `asyncio.gather`
  microbenchmark against the upstream, no framework involved, confirmed
  it independently of Hayavega or FastAPI); the raw-socket client built to
  replace it first reproduced this project's own previously-diagnosed
  Windows ephemeral-port/`TIME_WAIT` failure mode by opening a connection
  per call instead of reusing keep-alive ones; and the keep-alive fix
  then had to learn to parse `Transfer-Encoding: chunked`, which uvicorn
  turns out to send on persistent connections regardless of a declared
  `Content-Length` (verified independently with stdlib `http.client`). A
  fourth, separate design bug -- sharing one upstream process across all
  three sequentially-benchmarked targets, which let a killed target
  contaminate the next one with unclosed `CLOSE_WAIT` sockets -- was
  caught by a ~5x discrepancy between the same target measured against a
  contaminated vs. a fresh upstream, and fixed by giving each target its
  own freshly started upstream.
- Full account, all three runs, and the exact diagnostic trail for each
  bug: [`benchmarks/README.md`](benchmarks/README.md#real-concurrent-io-under-asgi-the-scenario-every-prior-comparison-left-open).

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
