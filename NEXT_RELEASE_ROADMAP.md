# Hayavega next-release roadmap

## Status: proposed (November 2026) -- v0.1.0 is live on PyPI; this plans what comes after it

`hayavega 0.1.0` was published to PyPI on 2026-11-12 via the trusted-publisher
GitHub Actions pipeline (`.github/workflows/release.yml`), verified end to end
with a real `pip install` and a real scaffolded project (`manage.py launch`,
auth, admin, migrations, GraphQL -- see the session history this roadmap was
written from). This document is the next chapter: what's left over from
earlier roadmaps that never got closed out, plus what a real release process
exposed that no prior roadmap anticipated.

As with every other roadmap in this repo, every claim here is meant to be
checked, not taken on faith -- a status marked delivered links to where to
verify it; a status marked deferred says why, not just that it was skipped.

## Where this roadmap's items came from

- **`UPCOMING_IMPROVEMENT_ROADMAP.md`** and **`INDUSTRY_LEADERSHIP_ROADMAP.md`**:
  items those documents explicitly left open (release notes, gRPC, the SPA
  framework) or marked stale (the docs-publish blocker).
- **`docs/superpowers/plans/2026-11-10-benchmark-credibility-plan.md`**'s own
  final review: two follow-up items the plan's reviewer explicitly deferred
  rather than folding into that plan's own fix wave.
- **A direct request this same session**, before the benchmark-credibility
  plan was scoped: performance gaps found but deliberately not prioritized
  at the time (multi-process scaling, free-threading, Rust build tuning,
  Postgres/MySQL pooling rigor).
- **Real release work**: gaps no prior roadmap could have anticipated,
  because they only became visible once this project actually went through
  a real PyPI release for the first time.

## Phase 1 -- Release process housekeeping (do this first; low-risk, high-leverage)

A public package with zero release notes anywhere is a real gap for a
project whose entire stated identity is "every claim is falsifiable and
documented" -- this phase closes that gap before anything else ships.

- [x] **Write `CHANGELOG.md`**, starting with a `v0.1.0` entry covering
      what actually shipped (the headline items: Signals, the Rust runtime,
      ASGI support, the benchmark suite, the security hardening pass, the
      scaffold fixes). Follows [Keep a Changelog](https://keepachangelog.com/)
      conventions, matching this project's existing preference for
      well-known formats over inventing its own (e.g. OpenAPI over a
      hand-rolled schema, Keep a Changelog over a bespoke log format). See
      `CHANGELOG.md`.
- [ ] **Create a real GitHub Release for the `v0.1.0` tag** -- deliberately
      **deferred, not done**, on explicit instruction: the project is not to
      be made more public right now, and a GitHub Release is a public-facing
      page. `CHANGELOG.md` already carries the same content a Release would
      show, so nothing is lost by leaving this undone; revisit only when
      asked to.
- [x] **Fix the stale "docs publish is blocked" language** in
      `UPCOMING_IMPROVEMENT_ROADMAP.md`'s Week 16 "Sequencing note" --
      found it directly contradicting the "Explicit publish step" bullet
      immediately above it, which already said the publish decision was
      resolved. Rewrote the note to match reality: the site is live and
      deploys are now gated to tagged commits only (see the Vercel item
      below); the one real remaining gap is the version-switcher
      acceptance criterion, which needs a second tagged version to exist,
      not an external decision.
- [x] **Establish a release checklist** for future tags: added
      `RELEASE_CHECKLIST.md` (update `CHANGELOG.md`, review
      `SECURITY_REVIEW_CHECKLIST.md`, tag, watch the publish workflow,
      create the GitHub Release, confirm the docs site redeployed).
      Disclosed honestly, not oversold: nothing in it is enforced by CI
      yet -- it is a manual checklist, and making it a real gate is its
      own open item.
- [x] **Gate Vercel deploys to tagged commits only**, added mid-roadmap
      on direct request: the live docs site was redeploying on every push
      and every PR, not just real releases. `vercel.json`'s
      `ignoreCommand` now skips the build unless the commit exactly
      matches a git tag -- verified live on a real PR (`Vercel ...
      Canceled by Ignored Build Step`).

### Acceptance criteria

- `CHANGELOG.md` exists, documents `v0.1.0` accurately, and is updated in
  the same change as any future user-facing release.
- `gh release view v0.1.0` (or the repo's Releases page) shows real notes,
  not a bare tag.
- No roadmap document asserts something about current state that a direct
  check (an HTTP request, a `gh` command) contradicts.

## Phase 2 -- Benchmark-credibility follow-ups (data already in hand)

Two items the final review of `docs/superpowers/plans/2026-11-10-benchmark-credibility-plan.md`
explicitly deferred as "can be a separate PR" rather than folding into that
plan's already-large fix wave.

- [x] **Publish the real Linux 8-way benchmark comparison.** Downloaded
      the latest clean `benchmark-linux` CI artifact (GitHub Actions run
      `43223535309`, post static_file-fix) and published it in
      `docs/performance.md` as a genuine Linux eight-way table, matching
      the existing disclosure conventions exactly (bold the real leader
      per column, disclose losses honestly -- Hayavega wins none of the
      four harder targets on Linux WSGI, unlike `db_list` on Windows --
      and cross-reference the already-documented WSGI/ASGI split, which
      is *more* pronounced in this fresher run: 91x on `db_list`, up from
      45x). Also refreshed the stale 4-way Linux table it sits beside
      (same CI run, one coherent dataset instead of two different-vintage
      ones side by side) -- `static_file`'s WSGI number moving 2342 to
      8678 (3.7x) is real Linux-side confirmation that this session's
      `static_file` regression fix is a genuine win, not a Windows-only
      artifact.
- [x] **`benchmark-linux`'s `run.py` no longer always exits 0.** Added a
      `--fail-on-error` flag (`unexpected_errors()`, verified directly
      with real assertions, not just read over) plus `KNOWN_ERROR_ALLOWLIST
      = {"Django"}` -- an explicit, documented allowlist, not a blanket
      "ignore all errors," so a *new* regression (e.g. the
      Granian/Robyn/Blacksheep install gap this session already found and
      fixed once) still fails the job loudly. Wired into
      `.github/workflows/ci.yml`'s `benchmark-linux` job. See
      `benchmarks/README.md`'s updated "Known, disclosed limitation"
      section.
- [x] **Rust runtime: `static_file` regression found and fixed.** A routine
      spot-check of the live docs site (2026-11-12) found the "Rust runtime
      vs. reference WSGI server" numbers in `docs/performance.md` no longer
      matched reality -- re-measured, `static_file` had flipped from a
      claimed 1.60x win to a real, reproducible 0.41-0.54x loss (confirmed
      at both concurrency 30 and concurrency 1, ruling out queuing effects).
      Real profiling (`py-spy` attached to the running server mid-benchmark,
      not a guess) found the actual cause: `hayavega.static`'s `serve()`
      called `Path.resolve()` (a real filesystem syscall) on every single
      request, accounting for over a third of that route's CPU time.
      **Fixed** in two steps, both via TDD: (1) cache the resolved,
      in-bounds path per request path, populated only on success so an
      attacker sending arbitrary never-repeating paths can't grow it
      unboundedly; (2) merge the separate `is_file()` check and content
      read into one `stat()`-based, mtime-invalidated cache, mirroring
      `templates.py`'s own existing `_compile_cached` pattern exactly (same
      "one syscall, not two" fix that module's own history already made).
      A file modified or removed after being cached is still detected
      correctly (fresh `stat()` every request) -- regression tests prove
      both. **Result: `static_file` is now a genuine 1.44-1.45x win**,
      confirmed across two clean re-measurements after clearing stale
      leftover processes that had silently corrupted an earlier
      measurement (the benchmark harness's own `wait_until_ready()` only
      polls the port, so it had been measuring an old process still
      running pre-fix code). Every scenario in this comparison is now a
      real win, not three wins and one disclosed loss. See
      `src/hayavega/static.py`, `tests/test_mvp_polish.py`'s
      `test_static_routes_caches_*`/`test_static_routes_serves_fresh_*`
      tests, and the updated numbers in `docs/performance.md`.

### Acceptance criteria

- `docs/performance.md`/`benchmarks/README.md` show real Linux numbers for
  all 8 targets, with the same disclosure rigor (bold leaders, disclosed
  losses) every other table in those documents already has.
- A deliberately-broken dependency in the `benchmark-linux` job (e.g.
  temporarily removing `granian` from its install line) causes the CI job
  to fail, not just a quietly-wrong artifact.
- Django's already-disclosed failure continues to show as an `error` row
  without failing the job, proving the allowlist works as intended.
- The `static_file` regression's root cause is identified via real
  profiling data and fixed with a re-measured, published before/after
  comparison -- **done**: 0.41-0.54x loss to 1.44-1.45x win, confirmed
  across two clean runs.

## Phase 3 -- Rust build tuning: investigated, not adopted (2026-11-12)

- [x] **`panic = "abort"` -- tested, rejected, a real correctness risk
      caught before shipping.** `crates/hayavega-python/src/server.rs`'s
      connection-handling loop deliberately wraps each connection in
      `std::panic::catch_unwind` so one panicking connection logs an error
      and is dropped, instead of taking the whole server process down --
      exactly the hardening concern `INDUSTRY_LEADERSHIP_ROADMAP.md`
      already flagged as dedicated work. `panic = "abort"` disables stack
      unwinding entirely, which makes `catch_unwind` a no-op: a single
      panicking connection would abort the whole process instead of being
      recovered. Rejected before it reached a benchmark, let alone a
      merge.
- [x] **`lto = true` + `codegen-units = 1` -- benchmarked, found
      negligible, not adopted.** Built and installed the extension with
      only these two settings (no `panic = "abort"`), ran the full Rust
      test suite (`cargo test --manifest-path crates/hayavega-core/Cargo.toml`,
      20/20 passing) and the hardening/TLS test suites (16 passed, 1
      skipped, same as baseline) to confirm no regression, then
      re-measured `benchmarks/rust_runtime_vs_wsgi.py` twice against the
      pre-change baseline. Result: every scenario landed within run-to-run
      noise of the unmodified build (`json_echo` 10546/10474/10518 req/s,
      `db_list` 3849/3998/4021, `template_render` 2694/2742/2711,
      `static_file` 7175/6919/6775 across baseline + two post-build runs).
      This server's bottleneck is I/O/syscall/GIL-bound, not CPU-bound
      Rust code LTO would speed up, so there is no decisive win to
      publish. Reverted; not merged. Following this project's own
      "benchmark first, wire in only on a decisive win" principle in the
      other direction: a measured non-win is reported honestly instead of
      shipped as a claimed one, matching `RUST_CORE_REWRITE_ROADMAP.md`'s
      own rejected-candidate precedent.

### Acceptance criteria

- A real before/after benchmark run (same methodology as every other
  performance claim in this repo: `oha`, multiple runs, real numbers)
  backs whatever improvement (or lack of one) gets published -- **done**:
  no improvement found, reported as such, nothing shipped.
- The existing Rust test suite (`cargo test -p hayavega-core`) and the
  Rust-runtime hardening tests (`tests/test_rust_runtime_hardening.py`)
  were verified against the candidate build before any benchmark ran --
  **done**, and this is what caught the `panic = "abort"` regression risk
  before it could reach production.

## Phase 4 -- Multi-process scaling and free-threading (the real structural ceiling; needs its own design pass before starting)

This is the biggest lever flagged and deliberately not prioritized earlier
this session in favor of the benchmark-credibility work. It is sized
correctly here as its own phase, not a quick item, because it is genuinely
larger than everything above combined.

- [x] **`SO_REUSEPORT` multi-process scaling for `hayavega_native.serve_wsgi()` -- shipped as a launcher, supervision still open.**
      Measured the GIL ceiling directly first: a `workers=1..32` sweep
      against `db_list` plateaus hard after 2-4 workers (2200 -> ~3900-4400
      req/s) regardless of thread count. `hayavega.rust_runtime.serve()`
      now takes `processes=N` (default 1, unchanged behavior): each process
      gets its own interpreter/GIL, all binding the same `host:port` via
      `SO_REUSEPORT`. Rejected on Windows with a clear error (no real
      equivalent there) rather than silently behaving differently. Verified
      for real on the `native-extension` CI job (Linux), not just locally --
      including catching and fixing a real bug first: `socket2`'s
      `set_reuse_port` is feature-gated behind `"all"`, invisible on this
      Windows dev machine because `cfg(unix)` excludes the whole code path
      here, so Cargo happily built without ever compiling it. After the
      fix, the real integration test (`tests/test_rust_runtime.py`'s
      `RustRuntimeReusePortTests`) proved two actual server processes can
      share one port and both serve requests. **Not done**: process
      supervision/restart-on-crash, and `hayavega launch`/`Settings.server`
      wiring a `processes` setting end-to-end -- this PR only lands the
      underlying capability. See
      `crates/hayavega-python/src/server.rs`, `src/hayavega/rust_runtime.py`,
      and `docs/topics/rust-runtime.md`.
- [ ] **Evaluate Python 3.13's free-threaded (no-GIL) build.** The one
      change that would remove the GIL ceiling structurally rather than
      working around it with more processes. Needs checking whether
      `hayavega_native` (PyO3-based) and this project's other C-extension
      dependencies (`msgspec`, `rusqlite` via the Rust side, `cryptography`)
      have free-threaded wheels or build support yet, before any real
      investment -- the ecosystem's free-threading support was still
      maturing as of this project's own knowledge cutoff, so this item may
      resolve to "revisit again in 6 months," which is a legitimate outcome
      to document, not a failure to deliver.

### Acceptance criteria (for whichever of the two, if either, proceeds past evaluation)

- A real multi-core benchmark (not just single-process throughput) backs
  any claim of improvement, following this project's own standard.
- No existing test (including the Rust-side hardening tests and the
  Signals dispatcher's concurrency tests) regresses.
- If free-threading's ecosystem support isn't ready, that's documented
  plainly as the finding, matching this project's practice of disclosing a
  negative result as thoroughly as a positive one (see
  `RUST_CORE_REWRITE_ROADMAP.md`'s own rejected-candidate table for the
  precedent).

## Phase 5 -- Postgres/MySQL connection-pooling rigor (parity with the SQLite work)

- [x] `hayavega.db.enable_connection_pooling()`/`enable_readonly_connection_pooling()`
      were benchmarked exhaustively for SQLite (see
      `INDUSTRY_LEADERSHIP_ROADMAP.md`'s "Connection pooling, attempted"
      section -- a real win for ASGI, a real regression for WSGI, both
      measured and disclosed). The same rigor has now been applied to
      PostgreSQL/MySQL.
- [x] **Benchmarked pooling against both real database engines under
      realistic concurrency.** Added
      `benchmarks/connection_pooling_postgres_mysql.py`, mirroring the
      SQLite investigation's own framework-free methodology (16 threads,
      200 queries each, pooled vs. unpooled, timed end to end), wired
      into the existing `postgresql-integration`/`mysql-integration` CI
      jobs -- reusing their already-wired `postgres:16`/`mysql:8` service
      containers, not a new environment. **Real result (GitHub Actions
      run `43242780767`): PostgreSQL 331 -> 3168 q/s (9.58x), MySQL 392
      -> 11052 q/s (28.20x)** -- a clean, decisive win for both engines,
      unlike SQLite's split result, which makes sense rather than
      contradicting it: PostgreSQL/MySQL are real client-server
      databases paying a TCP handshake plus authentication exchange on
      every fresh `connect()`, which SQLite's in-process, file-based
      model doesn't have. See `benchmarks/README.md`'s new "Connection
      pooling against real PostgreSQL/MySQL" section for the full
      writeup, including the one thing this did NOT measure (an
      HTTP-level confirmation through a real served app, since no
      PostgreSQL/MySQL benchmark app exists in this suite yet).

### Acceptance criteria

- Published, reproducible numbers for pooled vs. unpooled connections
  against both PostgreSQL and MySQL, matching the disclosure standard the
  SQLite work already set (including disclosing a regression if one is
  found, as the WSGI case already was for SQLite) -- **done**: both
  engines show a clean win, no regression found.

## Phase 6 -- Memory: investigated, found healthy, not a fix item

A direct request this session ("memory optimisation is also need to be
implemented in the next release") was investigated with the same real-data
discipline as the CPU-profiling work in Phase 2, using this project's own
established `tracemalloc`-peak methodology (`benchmarks/memory_profile_templates.py`;
RSS-based tools like `memory_profiler` show false-zero here due to CPython's
own refcounting freeing garbage immediately, and Scalene doesn't build on
Windows without MS C++ Build Tools).

- [x] **Per-request peak memory, measured across all four benchmarked
      routes** (`json_echo`, `db_list`, `template_render`, `static_file`),
      2000 calls each against the real `hayavega.runtime.create_application`
      WSGI callable. Result: 1.75-5.26 KiB median peak per call, with
      median == p90 == max on every route (no variance, no growth across
      calls). `template_render` is highest, plausibly from compiled-template
      execution context plus escaped HTML string construction -- expected,
      not wasteful.
- [x] **Stress-tested the two new caches added in Phase 2's `static_file`
      fix** (`_resolved`/`_content_cache` in `src/hayavega/static.py`) for
      unbounded-growth risk, since they hold one entry per distinct file
      path ever legitimately served for the life of the process. Served
      2000 distinct real files through them: ~1.6 KiB held per cached file,
      ~3.2 MiB total for 2000 files. Bounded by real files actually served
      (the attacker-controlled-growth vector was already closed when the
      cache was built -- only successful, in-bounds resolutions are cached),
      not a leak, but worth documenting as a real characteristic: a
      long-running process serving a very large, ever-changing set of static
      assets (e.g. thousands of user-uploaded files) will hold this cache's
      memory for the process lifetime. Fine for a typical project's
      asset count; a correct caveat to put in `docs/topics/deployment.md` if
      this framework is ever used for large-scale static/media hosting
      without a reverse proxy or CDN in front of it.
- [x] **No fixable inefficiency was found**, and none is invented here --
      current memory usage across the hot paths is already minimal. Nothing
      in this phase changes production code.
- [x] **Add a memory metric to the benchmark suite.** Added
      `benchmarks/memory_profile_routes.py`, promoting this session's
      investigation into a real, repo-committed script using the same
      `tracemalloc`-peak methodology, wired into the `perf` CI job
      (informational only -- no committed baseline/threshold yet, the
      same first step `db_list` took in `perf_gate.py` before its own
      gate was trusted). See `benchmarks/README.md`'s new "Per-request
      memory, measured in CI" section.
- [x] **Document the static-asset cache lifetime caveat** above in
      `docs/topics/deployment.md`'s existing static-files guidance --
      added a dedicated "Static files" section there.

### Acceptance criteria

- `docs/topics/deployment.md` states the static-cache lifetime
  characteristic plainly, with the real per-file cost measured here.
- `benchmarks/run.py` (or a new, adjacent script) reports a real
  memory number per target, using the same `tracemalloc`-peak methodology,
  not an estimate.
- No memory-related code change ships without a profiled before/after,
  matching the standard every performance claim in this repo already holds
  itself to -- including the possibility that the honest finding stays "no
  fix was needed."

## What stays deferred, correctly, and why

These remain open items in earlier roadmaps but are **not** part of this
roadmap, because no concrete need has emerged for any of them since they
were last declined:

- **gRPC support** (`UPCOMING_IMPROVEMENT_ROADMAP.md` Week 17) -- still no
  concrete polyglot-client or service-boundary need identified.
- **A Node.js/npm-based SPA framework** (Stage 2 of the UI framework,
  originally considered, re-scoped away from Node.js tooling, then the
  client-side-signals version itself removed) -- still no concrete need for
  real client-side reactivity beyond what the current hypermedia-only model
  provides.
- **Rust migration Phase 7 (auth internals) / Phase 8 (admin list-page
  rendering)** (`RUST_CORE_REWRITE_ROADMAP.md`) -- still correctly gated on
  "a profiler pointing at this specific code under real production load,"
  which doesn't exist yet.
- **HTTP/2 for the Rust runtime** -- still correctly out of scope; the
  reasoning in `INDUSTRY_LEADERSHIP_ROADMAP.md` (multiplexing doesn't fit
  the synchronous thread-per-connection design without becoming its own
  scheduler) hasn't changed.

## Definition of done

Matching the standard every other roadmap in this repo already holds
itself to:

1. Documented in the relevant guide (`CHANGELOG.md`, `README.md`,
   `benchmarks/README.md`, or `docs/`) in the same change as the
   user-facing capability.
2. Backed by tests, or -- for a performance claim specifically -- a real,
   reproducible benchmark result, never an estimate.
3. The full test suite, `ruff check`/`ruff format --check`, and `mypy` all
   pass.
4. No prior claim in this repo's documentation becomes false as a result,
   without being explicitly called out as a correction.
