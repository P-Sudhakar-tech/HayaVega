# Benchmark credibility — design spec

**Status:** approved design, ready for implementation planning.
**Date:** 2026-11-10

## Context

Hayavega's performance work to date (`INDUSTRY_LEADERSHIP_ROADMAP.md`,
`RUST_CORE_REWRITE_ROADMAP.md`, `docs/performance.md`) is already
unusually rigorous for this kind of claim: every number traces to a real
`oha` run, every loss is disclosed rather than smoothed over, and dead
ends (three rejected native-function ports, a rejected general Rust
template engine, a WSGI connection-pooling regression) are documented in
full. The goal of this spec is not to redo that work — it's to close the
specific gaps that would let an outside skeptic poke holes in the
"fastest Python framework" claim as it stands today:

1. **Measurement noise.** Every published number is a single `oha -c 30`
   run on one Windows dev machine. The project's own docs admit run-to-run
   swings up to 17% at this concurrency (see the `template_render`
   re-measurement in `INDUSTRY_LEADERSHIP_ROADMAP.md`).
2. **No Linux numbers.** The documented "environment ceiling" (SQLite +
   waitress topping out ~700-800 req/s) was diagnosed as a property of
   this specific Windows machine, but never re-tested on Linux, which is
   where most Python web apps actually run in production.
3. **Narrow comparison set.** Every comparison is against FastAPI and
   Django — not against the frameworks actually known for raw speed
   (Granian/RSGI, Robyn, Blacksheep) or an unframed raw-ASGI baseline.
4. **No regression protection.** All of this rigor is manual and
   session-by-session. Nothing stops a future change from silently
   eating back a previously-measured win.

Explicitly out of scope for this spec (flagged as real gaps, deferred by
direct user decision, candidates for a future roadmap): multi-process /
`SO_REUSEPORT` scaling, Python 3.13 free-threaded (no-GIL) evaluation,
`Cargo.toml` `[profile.release]` tuning, and deeper Postgres/MySQL
connection-pooling benchmarks.

## Goal

Every published performance claim should hold up on Linux, at a
concurrency that resembles real load, against the frameworks actually
competing on speed — and a regression should be caught by CI, not
rediscovered by a future session re-measuring from scratch.

## Phase A — Methodology foundation

**Why first:** every later phase (new comparison targets, a CI gate)
inherits whatever methodology this phase ships. Fixing it after adding
new targets would mean re-running everything twice.

### Deliverables

- A Linux CI job (`ubuntu-latest`) that runs the existing benchmark
  suite — `json_echo`, `db_list`, `template_render`, `static_file`, and
  the Rust-runtime-vs-FastAPI/WSGI comparisons — not just on the
  Windows dev machine.
- Raise concurrency from `oha -c 30` to `oha -c 200` (closer to a
  realistic production load) across the suite.
- Run each scenario N=5 times and report the median plus a spread
  (min/max), not a single number, directly addressing the documented
  noise band.
- Re-run every scenario whose published number might shift under the
  new methodology and disclose the change the same way the existing
  `template_render` re-measurement was disclosed — as a re-measurement
  with reasoning, never a silent correction.

### Acceptance criteria

- `benchmarks/README.md` and `docs/performance.md` show Linux and
  Windows numbers side by side, each clearly labeled by environment.
- CI runs the benchmark suite on every push/PR that touches
  `benchmarks/`, `src/hayavega/`, or `crates/`.
- Any number that changes under the new methodology links to an
  explanation, matching this project's existing disclosure standard.

## Phase B — Competitive benchmark set

**Why second:** new comparison numbers are only meaningful once Phase
A's methodology is trustworthy; publishing them on the old single-run,
`-c 30` methodology would mean redoing this phase anyway.

### Deliverables

- Four new benchmark apps, each reusing the existing seeded `Post` data
  and the same four scenarios already used for FastAPI/Django:
  - **Granian** (Rust-core ASGI/RSGI server) — the most direct
    competitor, since it's in the same "Rust core, Python views"
    category as Hayavega's own Rust runtime.
  - **Robyn** (Rust-core Python framework).
  - **Blacksheep** (fast pure-Python ASGI framework).
  - **Raw ASGI "hello world"** with no framework at all, as an honest
    ceiling.
- Every new app's output is verified byte-identical to the existing
  apps' responses before any timing run, matching the correctness-first
  discipline every existing comparison app already follows.
- Run on Phase A's methodology (Linux, `-c 200`, N=5) so results are
  directly comparable to the existing table, not a second, inconsistent
  baseline.

### Acceptance criteria

- `benchmarks/README.md` gains a new comparison table including all
  four new targets.
- Any scenario where a new target wins gets its own disclosed "why"
  writeup, with the same rigor as every existing loss entry (e.g. the
  three rejected native-function candidates, the WSGI pooling
  regression).

## Phase C — CI performance-regression gate

**Why last:** gating requires a stable baseline, which only exists once
Phases A and B have shipped a methodology and a number worth protecting.

### Deliverables

- A dedicated `perf` CI job (separate from `lint`/`typecheck`/`test`,
  matching this repo's existing one-job-per-concern pattern) running
  `json_echo` and `db_list` on every PR touching `src/hayavega/`,
  `crates/`, or `benchmarks/`.
- A stored baseline (committed JSON/text, not regenerated per run) and a
  documented regression threshold — 15%, chosen to sit above Phase A's
  measured noise band so the gate doesn't fire on noise alone.
- An explicit, maintainer-run command to refresh the baseline after a
  real, intentional change — never refreshed silently by the gate
  itself, so a regression can't get locked in as the new normal.

### Acceptance criteria

- A deliberately introduced regression (e.g. temporarily reverting the
  `Selection.values()` row-hydration fix) is caught by the gate in CI.
- A baseline refresh is a visible, reviewed action (a commit touching
  the baseline file), not an automatic side effect of a passing run.

## Risks

- The three new framework apps (Granian, Robyn, Blacksheep) are
  unfamiliar to this codebase; each needs its own correctness
  verification before timing, the same discipline every existing
  comparison app got.
- Linux numbers diverging from Windows ones may reopen previously
  "closed" findings (e.g. a fix that showed "no HTTP-level effect" on
  Windows might behave differently on Linux). This is Phase A doing its
  job, not a sign something is wrong — but it means Phase A may surface
  new follow-up work, not just confirm old results.

## Definition of done

Matching the standard already set in `UPCOMING_IMPROVEMENT_ROADMAP.md`
and `INDUSTRY_LEADERSHIP_ROADMAP.md`:

1. Documented in `benchmarks/README.md` and `docs/performance.md`.
2. Backed by a real, reproducible benchmark run — never an estimate.
3. The full test suite passes.
4. No prior claim in this repo's documentation becomes false as a
   result without being explicitly called out as a re-measurement.
