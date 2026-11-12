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

- [ ] **Write `CHANGELOG.md`**, starting with a `v0.1.0` entry covering
      what actually shipped (the headline items: Signals, the Rust runtime,
      ASGI support, the benchmark suite, the security hardening pass, the
      scaffold fixes). Follow [Keep a Changelog](https://keepachangelog.com/)
      conventions, matching this project's existing preference for
      well-known formats over inventing its own (e.g. OpenAPI over a
      hand-rolled schema, Keep a Changelog over a bespoke log format).
- [ ] **Create a real GitHub Release for the `v0.1.0` tag**, retroactively,
      with notes drawn from the new `CHANGELOG.md` entry. `gh release list`
      currently returns nothing -- the tag exists, but there is no release
      page a visitor would actually find.
- [ ] **Fix the stale "docs publish is blocked" language** in
      `UPCOMING_IMPROVEMENT_ROADMAP.md`'s Week 16 section. The docs site is
      confirmed live at `https://hayavega.vercel.app/` (verified via a real
      HTTP request, title tag matches real content) -- the roadmap text
      still describes this as gated on an unresolved "go public" decision,
      which is no longer true and should not mislead the next reader.
- [ ] **Establish a release checklist** for future tags: update
      `CHANGELOG.md`, create the GitHub Release, confirm `SECURITY_REVIEW_CHECKLIST.md`
      has been reviewed (it already says "review this checklist before each
      production release" -- nothing currently enforces that happening).

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

- [ ] **Publish the real Linux 8-way benchmark comparison.** A real
      `benchmark-linux` CI run already produced genuine throughput numbers
      for all 8 targets, including Granian/Robyn/Blacksheep on Linux (e.g.
      Granian measured 65124 req/s on `json_echo` in a verified run) -- this
      data exists in a CI artifact and was never written into
      `docs/performance.md` or `benchmarks/README.md`. Download the latest
      clean `benchmark-linux` artifact, verify it against
      `benchmarks/results/RESULTS.md`'s existing disclosure conventions
      (bold the real leader per column, disclose losses, cross-reference the
      already-documented WSGI/waitress Linux anomaly), and publish it as a
      genuine Linux eight-way table alongside the existing Windows one.
- [ ] **`benchmark-linux`'s `run.py` always exits 0, regardless of per-target
      errors.** This was a Review Focus item in the benchmark-credibility
      plan that was never actually implemented -- `benchmark_target()`'s
      existing `except Exception: result.error = str(error)` records a
      failure in the published table, but nothing makes the CI job itself
      fail when that happens, so a silently-broken target (e.g. a missing
      dependency reintroducing the exact `granian`/`robyn`/`blacksheep`
      install gap this session already found and fixed once) would stay
      invisible unless someone reads the artifact by hand. Add a
      `--fail-on-error` flag to `benchmarks/run.py`, with an explicit,
      documented allowlist for Django (the one target with a disclosed,
      accepted, CI-environment-specific failure) so the job can fail loudly
      on a *new* regression without permanently red-lighting on a known one.

### Acceptance criteria

- `docs/performance.md`/`benchmarks/README.md` show real Linux numbers for
  all 8 targets, with the same disclosure rigor (bold leaders, disclosed
  losses) every other table in those documents already has.
- A deliberately-broken dependency in the `benchmark-linux` job (e.g.
  temporarily removing `granian` from its install line) causes the CI job
  to fail, not just a quietly-wrong artifact.
- Django's already-disclosed failure continues to show as an `error` row
  without failing the job, proving the allowlist works as intended.

## Phase 3 -- Rust build tuning (free, zero-risk win, never attempted)

- [ ] **Add `[profile.release]` to `Cargo.toml`**: `lto = true`,
      `codegen-units = 1`, `panic = "abort"`. No `[profile.release]` section
      currently exists at all in this workspace, meaning every native build
      (the Rust runtime, `SerializedConnection`, the GraphQL lexer/parser,
      the Signals dispatcher) has shipped on Rust's unoptimized defaults the
      entire time. Benchmark before and after against the existing Rust
      runtime vs. FastAPI/waitress suites
      (`benchmarks/rust_runtime_vs_fastapi.py`, `benchmarks/rust_runtime_vs_wsgi.py`)
      before publishing any new performance claim, following this project's
      own standing "benchmark first, wire in only on a decisive win"
      principle -- `panic = "abort"` in particular changes unwinding
      behavior across the PyO3 boundary and needs verifying it doesn't
      change error-handling behavior for Python callers, not just assumed
      safe because it's a common recommendation.

### Acceptance criteria

- A real before/after benchmark run (same methodology as every other
  performance claim in this repo: `oha`, multiple runs, real numbers)
  backs whatever improvement (or lack of one) gets published.
- The existing Rust test suite (`cargo test -p hayavega-core`) and the
  Rust-runtime hardening tests (`tests/test_rust_runtime_hardening.py`)
  still pass with `panic = "abort"` -- a hand-written HTTP server's panic
  recovery behavior under adversarial input (already a dedicated concern in
  `INDUSTRY_LEADERSHIP_ROADMAP.md`'s hardening pass) must not regress.

## Phase 4 -- Multi-process scaling and free-threading (the real structural ceiling; needs its own design pass before starting)

This is the biggest lever flagged and deliberately not prioritized earlier
this session in favor of the benchmark-credibility work. It is sized
correctly here as its own phase, not a quick item, because it is genuinely
larger than everything above combined.

- [ ] **Evaluate `SO_REUSEPORT` multi-process scaling for `hayavega_native.serve_wsgi()`.**
      The Rust runtime is currently one process with a fixed thread pool --
      CPython's GIL means only one thread executes Python view code at a
      time regardless of pool size. A `gunicorn`-style `-w N` multi-process
      model (each process its own GIL) is the direct way to use more than
      one CPU core. Needs its own scoping: how worker processes are
      supervised/restarted, how `Settings.server`'s existing `engine =
      "rust"` opt-in exposes a worker count, and a real multi-core benchmark
      (this project's benchmarking has so far run single-process throughput
      numbers almost exclusively).
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

- [ ] `hayavega.db.enable_connection_pooling()`/`enable_readonly_connection_pooling()`
      were benchmarked exhaustively for SQLite (see
      `INDUSTRY_LEADERSHIP_ROADMAP.md`'s "Connection pooling, attempted"
      section -- a real win for ASGI, a real regression for WSGI, both
      measured and disclosed). The same rigor has never been applied to the
      PostgreSQL/MySQL integration paths (`tests/test_postgresql_integration.py`,
      `tests/test_mysql_integration.py`), which currently only verify
      correctness, not throughput under concurrency.
- [ ] Benchmark pooling behavior against both real database engines under
      realistic concurrency (CI's existing `postgres:16`/`mysql:8` service
      containers are already wired for correctness tests -- reuse them for
      a throughput comparison, not a new environment).

### Acceptance criteria

- Published, reproducible numbers for pooled vs. unpooled connections
  against both PostgreSQL and MySQL, matching the disclosure standard the
  SQLite work already set (including disclosing a regression if one is
  found, as the WSGI case already was for SQLite).

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
