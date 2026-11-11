# Benchmark credibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Hayavega's "fastest Python framework" performance claims hold up on Linux, at realistic concurrency, against the frameworks actually competing on raw speed, and protect them from silent regression.

**Architecture:** Extend the existing `benchmarks/run.py` orchestrator (subprocess servers + `oha`, already used for the FastAPI/Django/Hayavega comparison) rather than building a parallel tool: add repeated-run statistics and a Linux CI job (Phase A), four new `*_app/` comparison targets following the exact pattern `benchmarks/fastapi_app/` already establishes (Phase B), and a narrow, fast `perf_gate.py` script with a committed JSON baseline wired into its own CI job (Phase C).

**Tech Stack:** Python 3.12, `oha` (Rust load generator, already a dependency of this suite), GitHub Actions (`ubuntu-latest`, matching every other job in `.github/workflows/ci.yml`), Granian, Robyn, Blacksheep (new benchmark-only dependencies, never imported by `src/hayavega`).

**Spec:** `docs/superpowers/specs/2026-11-10-benchmark-credibility-design.md`

## Global Constraints

- Every number published must trace to a real `oha` run — never an estimate (spec Definition of Done #2).
- Concurrency for new/re-run scenarios: `oha -c 200` (spec Phase A), replacing the historical `-c 30`.
- Repeat count for statistical reporting: N=5 per scenario, reporting median + min/max spread (spec Phase A).
- New Linux benchmark CI job runs on `ubuntu-latest` (matching every existing job in `.github/workflows/ci.yml`).
- Perf-regression threshold: 15%, chosen to sit above Phase A's measured noise band (spec Phase C).
- Baseline refresh is an explicit, maintainer-run action — never automatic (spec Phase C).
- Any number that changes under the new methodology is disclosed as a re-measurement with reasoning, never silently corrected (spec Definition of Done #4, matching this project's existing practice in `INDUSTRY_LEADERSHIP_ROADMAP.md`).
- Out of scope, do not touch: multi-process/`SO_REUSEPORT`, Python 3.13 free-threading, `Cargo.toml` `[profile.release]` tuning, Postgres/MySQL connection-pooling benchmarks.
- `benchmarks/` is outside `testpaths` and the 100%-coverage gate (`pyproject.toml`) — this project's established pattern for this directory is verification by actually running the script and inspecting real output, not pytest unit tests. Follow that pattern; do not add `benchmarks/` to `testpaths`.

## Review Focus

- **A scenario where a new comparison target (Granian/Robyn/Blacksheep) returns a response that differs from the existing apps' shape** (extra/missing JSON keys, different HTML) would make a "win" or "loss" meaningless — each new app's output must be diffed against the existing `benchmarks/hayavega_app` output before any timing run, not just assumed correct from reading the framework's docs.
- **A new target's server process failing to start in CI** (missing system dependency, port conflict, Robyn's binary wheel unavailable for the CI runner's platform) must be caught as a clear job failure, not silently skipped — `benchmark_target()`'s existing `except Exception: result.error = str(error)` path already records this, but the new Linux CI job must still fail its own step if any target errors, not swallow it as "the script ran."
- **The perf gate running on a PR from a fork** (no write access to update the baseline, and potentially flaky shared CI runner performance) could produce a false-positive regression failure — the gate must report the actual vs. baseline numbers in its failure message so a maintainer can judge noise vs. real regression, not just emit a bare non-zero exit code.
- **A baseline refresh accidentally run against an already-regressed `main`** would silently bake a regression in as the new normal — the refresh command must print the old and new baseline values so the action is visible in the commit that updates `perf_baseline.json`, not a silent file overwrite.
- **Five repeated 10-second runs per scenario per target, times four new targets, in CI** could make the Linux benchmark job slow enough to be skipped/ignored in practice — Phase A's job must use a CI-appropriate duration (shorter than a local full run) while keeping the methodology (N=5, `-c 200`, median+spread) intact, documented as a deliberate CI/local distinction, not a silent weakening of the methodology.

---

## Phase A — Methodology foundation

### Task 1: Repeated-run statistics in `benchmarks/run.py`

**Files:**
- Modify: `benchmarks/run.py`

**Interfaces:**
- Produces: `AggregatedResult` dataclass (`requests_per_sec_median: float`, `requests_per_sec_min: float`, `requests_per_sec_max: float`, `p50_ms_median: float`, `success_rate_min: float`) used by Task 2 and Task 8.
- Produces: `benchmark_target(target, *, duration, connections, warmup_requests, scenarios=None, runs=1) -> TargetResult` — `TargetResult.scenarios` becomes `dict[str, list[ScenarioResult]]` (one entry per run) when `runs > 1`; existing callers that pass `runs=1` (the default) keep today's single-result-per-scenario behavior unchanged.
- Produces: `aggregate(results: list[ScenarioResult]) -> AggregatedResult` (module-level function), using `statistics.median` for `requests_per_sec_median`/`p50_ms_median`, `min`/`max` over the raw list for spread.
- Consumes: existing `run_oha`, `wait_until_ready`, `Target`, `ScenarioResult`, `TargetResult` (all already defined in this file).

- [ ] **Step 1: Add `--runs` CLI argument**

In `main()`, add `parser.add_argument("--runs", type=int, default=1, help="Repeat each scenario this many times and report median + spread (default: 1)")`. Pass `runs=args.runs` through to `benchmark_target`.

- [ ] **Step 2: Implement `aggregate(results: list[ScenarioResult]) -> AggregatedResult`**

Use `statistics.median` (stdlib `statistics` module, new import) over `[r.requests_per_sec for r in results]` and `[r.p50_ms for r in results]`; `min`/`max` directly over `requests_per_sec` for spread; `min` over `success_rate` (the worst run's success rate, never averaged away).

- [ ] **Step 3: Change `benchmark_target` to loop `runs` times per scenario**

Inside the existing `for name, method, path in scenarios:` loop, call `run_oha` `runs` times into a list, store the list in `result.scenarios[name]` (so `TargetResult.scenarios: dict[str, list[ScenarioResult]]`). Keep one warm-up call per *target* (not per run) exactly as today — warm-up amortizes import/DB-creation cost once, not once per statistical sample.

- [ ] **Step 4: Update `render_markdown` to report median + spread when `runs > 1`**

When a scenario has more than one `ScenarioResult`, render `{median:.0f} (min {min:.0f}, max {max:.0f})` for req/s instead of a bare number; a single-run scenario (`runs=1`, the existing default) renders exactly as today, so the existing `RESULTS.md` format for single runs is unchanged.

- [ ] **Step 5: Run it manually to verify**

Run: `python benchmarks/run.py --duration 3s --connections 10 --runs 2` (short duration/connections for a fast manual check, not the real methodology values)
Expected: completes without error; `benchmarks/results/RESULTS.md` shows a median+spread format for each scenario.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/run.py
git commit -m "feat: add repeated-run median/spread reporting to the benchmark suite"
```

### Task 2: Linux benchmark CI job

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `benchmarks/run.py --duration <D> --connections 200 --runs 5` (Task 1).

- [ ] **Step 1: Add a `benchmark-linux` job to `.github/workflows/ci.yml`**

```yaml
benchmark-linux:
  runs-on: ubuntu-latest
  if: github.event_name == 'push'
  steps:
    - uses: actions/checkout@v7
    - uses: actions/setup-python@v7
      with:
        python-version: "3.12"
    - uses: dtolnay/rust-toolchain@stable
    - name: Install oha
      run: cargo install oha --locked
    - name: Install Hayavega and comparison frameworks
      run: pip install -e ".[dev]" fastapi "uvicorn[standard]" jinja2 django waitress
    - name: Run the benchmark suite (CI-appropriate duration, full concurrency/repeat methodology)
      run: python benchmarks/run.py --duration 5s --connections 200 --runs 5
    - name: Upload results
      uses: actions/upload-artifact@v4
      with:
        name: benchmark-results-linux
        path: benchmarks/results/RESULTS.md
```

Gated on `push` (not every PR) since it's a ~10+ minute job (4 targets × 4 scenarios × 5 runs × 5s) — matching how `dependency-scan` and other heavier jobs in this file are not duplicated per-PR-push-to-a-fork noise; follow whichever existing job's trigger pattern is closest once you re-read the current file, since this file changes under Task 2/4/9's hands too.

- [ ] **Step 2: Verify the job's YAML is valid and the job name doesn't collide**

Run: `python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text())"` (or equivalent YAML lint) to confirm the file still parses.
Expected: no exception; `benchmark-linux` appears once under `jobs:`.

- [ ] **Step 3: Push to a branch and confirm the job runs and completes in GitHub Actions**

Run: `gh run watch` (or `gh run list`/`gh run view` as used elsewhere in this project's own sessions) after pushing.
Expected: `benchmark-linux` job completes successfully; the `benchmark-results-linux` artifact is produced; no target in the uploaded `RESULTS.md` shows an `error:` cell.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add a Linux benchmark job running the full suite on every push"
```

### Task 3: Publish Linux numbers alongside Windows, labeled

**Files:**
- Modify: `docs/performance.md`
- Modify: `benchmarks/README.md`

**Interfaces:**
- Consumes: the `benchmark-results-linux` artifact from Task 2's CI run (download via `gh run download` or the Actions UI), and a fresh local Windows run via `python benchmarks/run.py --duration 10s --connections 200 --runs 5` for the updated Windows-side numbers under the new methodology.

- [ ] **Step 1: Re-run the benchmark suite under the new methodology on Windows**

Run: `python benchmarks/run.py --duration 10s --connections 200 --runs 5`
Expected: completes; note the new median numbers for comparison against the historical `-c 30`, single-run numbers already in `docs/performance.md`.

- [ ] **Step 2: Update `docs/performance.md`'s headline table**

Add an explicit "Environment" column or a labeled sub-heading distinguishing Windows (dev machine) from Linux (CI) results; keep both — do not delete the historical Windows single-run numbers without first checking whether anything in `INDUSTRY_LEADERSHIP_ROADMAP.md` links to them as a specific disclosed finding (several do — e.g. the "environment ceiling" result). If a number moved meaningfully under the new methodology, add one sentence noting it's a re-measurement, following the exact disclosure pattern `INDUSTRY_LEADERSHIP_ROADMAP.md`'s "Re-measuring the WSGI `template_render` gap" section already uses.

- [ ] **Step 3: Update `benchmarks/README.md`'s "Running it" section**

Document the `--runs` flag and the new default recommended invocation (`--connections 200 --runs 5`), keeping the existing `--duration`/`--connections` flag docs intact (just updated recommended values).

- [ ] **Step 4: Commit**

```bash
git add docs/performance.md benchmarks/README.md
git commit -m "docs: publish Linux benchmark numbers alongside Windows, both labeled"
```

---

## Phase B — Competitive benchmark set

### Task 4: Raw ASGI baseline app

**Files:**
- Create: `benchmarks/raw_asgi_app/app.py`
- Create: `benchmarks/raw_asgi_app/static/logo.svg` (copy of the existing `benchmarks/fastapi_app/static/logo.svg` — same 213-byte file every other target serves, so `static_file` stays an apples-to-apples comparison)
- Create: `benchmarks/raw_asgi_app/templates/page.html` (copy of `benchmarks/fastapi_app/templates/page.html`'s markup, rendered with Python string formatting instead of Jinja2 — this app has no template engine, deliberately)

**Interfaces:**
- Produces: `application` (an ASGI 3.0 callable: `async def application(scope, receive, send)`), importable as `benchmarks.raw_asgi_app.app:application`, serving the same four routes (`GET /echo`, `GET /posts.json`, `GET /posts`, `GET /static/logo.svg`) and the same seeded 20-row SQLite schema (`Post{title, views}`, `SEED_COUNT = 20`) as `benchmarks/fastapi_app/app.py`.
- Consumes: nothing from earlier tasks — this is a from-scratch, dependency-free ASGI app (stdlib `sqlite3` only), matching the "honest ceiling" role the spec assigns it.

- [ ] **Step 1: Implement `seed()`/`fetch_posts()` identically to `benchmarks/fastapi_app/app.py`'s versions**

Same `DATABASE` path pattern (`Path(tempfile.gettempdir()) / "raw_asgi_benchmark.sqlite3"`), same `SEED_COUNT = 20`, same table schema and query — copy verbatim, changing only the filename, so the DB-layer cost is identical across every target.

- [ ] **Step 2: Implement `application(scope, receive, send)` with manual routing**

Dispatch on `scope["path"]` + `scope["method"]` to four handlers: `/echo` returns `{"status": "ok"}` as JSON; `/posts.json` returns `fetch_posts()` as JSON; `/posts` renders `page.html`'s markup via `str.format`/an f-string loop (no templating library) over `fetch_posts()`; `/static/logo.svg` reads and returns the SVG file with `content-type: image/svg+xml`. A path that matches none of these returns a 404.

- [ ] **Step 3: Verify response shapes match `benchmarks/fastapi_app` byte-for-byte on the JSON endpoints**

Run: start both apps locally (`python -m uvicorn benchmarks.raw_asgi_app.app:application --port 9020` and the existing FastAPI target), then `python -c "import urllib.request,json; a=json.load(urllib.request.urlopen('http://127.0.0.1:9002/posts.json')); b=json.load(urllib.request.urlopen('http://127.0.0.1:9020/posts.json')); assert a==b, (a,b)"`.
Expected: no `AssertionError` — identical JSON.

- [ ] **Step 4: Commit**

```bash
git add benchmarks/raw_asgi_app/
git commit -m "feat: add a raw ASGI baseline benchmark app (no framework)"
```

### Task 5: Granian target (serves the raw ASGI app)

**Files:**
- Modify: `benchmarks/run.py` (add one `Target` entry; the full wiring happens in Task 8, this task only proves Granian can serve `raw_asgi_app` standalone)
- Modify: `benchmarks/README.md` (installation note)

**Interfaces:**
- Consumes: `benchmarks.raw_asgi_app.app:application` (Task 4) — Granian is a server, not a framework, so it serves the same app `uvicorn` would, with no new application code needed.

- [ ] **Step 1: Verify Granian can serve the raw ASGI app standalone**

Run: `pip install granian` then `granian --interface asgi benchmarks.raw_asgi_app.app:application --host 127.0.0.1 --port 9021`, then `curl http://127.0.0.1:9021/echo`.
Expected: `{"status": "ok"}`.

- [ ] **Step 2: Document the install command in `benchmarks/README.md`'s "Running it" section**

Add `granian` to the `pip install` line alongside the existing `fastapi "uvicorn[standard]" jinja2 django waitress`.

- [ ] **Step 3: Commit**

```bash
git add benchmarks/README.md
git commit -m "docs: document the Granian dependency for the new benchmark target"
```

### Task 6: Robyn comparison app

**Files:**
- Create: `benchmarks/robyn_app/app.py`
- Create: `benchmarks/robyn_app/static/logo.svg` (same file as Task 4)

**Interfaces:**
- Produces: a Robyn `app` instance exposing the same four routes, runnable via `python benchmarks/robyn_app/app.py` (Robyn's own convention — it is not WSGI/ASGI-served by an external server the way the other targets are; confirm this against Robyn's current documentation when implementing, since its serving model is its own, not uvicorn/waitress).
- Consumes: same `seed()`/`fetch_posts()` pattern as Task 4, against its own SQLite file (`robyn_benchmark.sqlite3`).

- [ ] **Step 1: Implement the four routes using Robyn's routing API**

`@app.get("/echo")`, `@app.get("/posts.json")`, `@app.get("/posts")` (render the same markup Task 4's `/posts` does — Robyn has no built-in templating, same honest-comparison reasoning as the raw ASGI app), and Robyn's static-file serving for `/static/logo.svg` (check Robyn's current static-file API — `app.add_directory` or equivalent).

- [ ] **Step 2: Verify response shapes match the raw ASGI app byte-for-byte on `/posts.json`**

Run: start both apps, same comparison script pattern as Task 4 Step 3.
Expected: identical JSON.

- [ ] **Step 3: Commit**

```bash
git add benchmarks/robyn_app/
git commit -m "feat: add a Robyn benchmark app"
```

### Task 7: Blacksheep comparison app

**Files:**
- Create: `benchmarks/blacksheep_app/app.py`
- Create: `benchmarks/blacksheep_app/static/logo.svg` (same file as Task 4)

**Interfaces:**
- Produces: `application` (an ASGI callable, Blacksheep apps are plain ASGI), importable as `benchmarks.blacksheep_app.app:application`, served by `uvicorn` exactly like `benchmarks/fastapi_app` is.
- Consumes: same `seed()`/`fetch_posts()` pattern as Task 4, against its own SQLite file (`blacksheep_benchmark.sqlite3`).

- [ ] **Step 1: Implement the four routes using Blacksheep's routing API**

Blacksheep has Jinja2 integration available (`blacksheep.server.rendering.jinja2`) — use it for `/posts` so this target represents Blacksheep's own idiomatic templating path, same as FastAPI's own Jinja2 use, not the no-templating stand-in Tasks 4/6 use. Serve static files via Blacksheep's `app.serve_files` (or current equivalent).

- [ ] **Step 2: Verify response shapes match the raw ASGI app byte-for-byte on `/posts.json`**

Same verification pattern as Task 4 Step 3 / Task 6 Step 2.

- [ ] **Step 3: Commit**

```bash
git add benchmarks/blacksheep_app/
git commit -m "feat: add a Blacksheep benchmark app"
```

### Task 8: Wire all four new targets into `benchmarks/run.py`

**Files:**
- Modify: `benchmarks/run.py`

**Interfaces:**
- Consumes: `benchmarks.raw_asgi_app.app:application` (Task 4), `benchmarks.robyn_app.app` (Task 6, run directly per Robyn's own convention — not via uvicorn), `benchmarks.blacksheep_app.app:application` (Task 7), and Granian serving `benchmarks.raw_asgi_app.app:application` (Task 5).
- Produces: `build_targets()` returning 8 `Target` entries total (the existing 4 plus these new 4).

- [ ] **Step 1: Add four `Target` entries to `build_targets()`**

Ports `9005`-`9008` (next free after the existing `9001`-`9004`): `"Raw ASGI"` (uvicorn, port 9005), `"Granian"` (the `granian --interface asgi ... --port 9006` command, serving `raw_asgi_app`), `"Robyn"` (Robyn's own run command, port 9007 — set via however Robyn's app configures its port, likely an environment variable or the app's own `app.start(port=9007)` call inside `benchmarks/robyn_app/app.py`'s `if __name__ == "__main__":` block), `"Blacksheep"` (uvicorn, port 9008).

- [ ] **Step 2: Run the full suite with all 8 targets**

Run: `python benchmarks/run.py --duration 5s --connections 50 --runs 1` (short values for a fast manual sanity check before the real methodology run)
Expected: all 8 targets report `ok` (no `error:` entries) in the console summary; `RESULTS.md` has 8 rows per scenario table.

- [ ] **Step 3: Run the real methodology (Phase A's values) and update published numbers**

Run: `python benchmarks/run.py --duration 10s --connections 200 --runs 5`
Then update `benchmarks/README.md` and `docs/performance.md` with the new 8-way comparison table, disclosing any scenario where a new target wins (per spec Phase B acceptance criteria) with the same rigor as existing disclosed losses.

- [ ] **Step 4: Commit**

```bash
git add benchmarks/run.py benchmarks/README.md docs/performance.md
git commit -m "feat: add Granian, Robyn, Blacksheep, and raw-ASGI targets to the benchmark suite"
```

---

## Phase C — CI performance-regression gate

### Task 9: `perf_gate.py` and a committed baseline

**Files:**
- Create: `benchmarks/perf_gate.py`
- Create: `benchmarks/results/perf_baseline.json`

**Interfaces:**
- Produces: `python benchmarks/perf_gate.py` (CLI, no args) — runs `json_echo` and `db_list` against Hayavega (WSGI) only, at a CI-appropriate duration (`5s`, `-c 50`, 1 run — a regression gate needs to be fast on every PR, unlike Phase A's full statistical methodology), compares each scenario's `requests_per_sec` against `perf_baseline.json`'s stored value, and exits non-zero (printing both the measured and baseline numbers, and the percentage delta) if either drops by more than 15%.
- Produces: `python benchmarks/perf_gate.py --refresh-baseline` — re-runs the same measurement and overwrites `perf_baseline.json`, printing the old and new values for both scenarios before writing.
- Consumes: `benchmark_target`, `build_targets` (reuse only the `"Hayavega"` entry), `run_oha` (all from `benchmarks/run.py`, Task 1's version).

- [ ] **Step 1: Define `perf_baseline.json`'s schema and seed it**

`{"json_echo": {"requests_per_sec": <float>}, "db_list": {"requests_per_sec": <float>}}`. Seed its initial values by running the gate's own measurement once (`python benchmarks/perf_gate.py --refresh-baseline` after Step 2 is implemented) rather than guessing numbers by hand.

- [ ] **Step 2: Implement `perf_gate.py`**

Reuse `benchmarks.run.build_targets()`, filter to the `"Hayavega"` target, call `benchmark_target(target, duration="5s", connections=50, warmup_requests=50, scenarios=[s for s in SCENARIOS if s[0] in {"json_echo", "db_list"}])`. For each scenario, compute `pct_change = (measured - baseline) / baseline`; if `pct_change < -0.15`, record a failure line `f"{scenario}: {measured:.0f} req/s vs baseline {baseline:.0f} req/s ({pct_change:.1%})"`. Print every scenario's numbers regardless of pass/fail (Review Focus: a maintainer judging noise needs the actual numbers, not a bare failure). Exit code 1 if any scenario failed, else 0.

- [ ] **Step 3: Implement `--refresh-baseline`**

`argparse` flag; when set, run the same measurement, print `f"{scenario}: {old:.0f} -> {new:.0f} req/s"` for each scenario before overwriting, then write the new `perf_baseline.json` (pretty-printed, stable key order) and exit 0 without checking the threshold.

- [ ] **Step 4: Verify the gate passes against its own fresh baseline**

Run: `python benchmarks/perf_gate.py --refresh-baseline` then `python benchmarks/perf_gate.py`
Expected: second run prints both scenarios' numbers and exits 0 (measuring against the baseline just set, so no regression).

- [ ] **Step 5: Verify the gate fails on a deliberate regression**

Temporarily revert the `Selection.values()` row-hydration optimization in `src/hayavega/models.py` (per the spec's suggested regression-injection method), run `python benchmarks/perf_gate.py`, confirm non-zero exit and a failure line naming `db_list`, then revert the temporary change (`git checkout -- src/hayavega/models.py`).
Expected: non-zero exit, `db_list` reported as a failure with both numbers shown; after reverting, confirm `git status` shows no leftover changes to `models.py`.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/perf_gate.py benchmarks/results/perf_baseline.json
git commit -m "feat: add a fast CI performance-regression gate for Hayavega's own throughput"
```

### Task 10: `perf` CI job

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `benchmarks/perf_gate.py` (Task 9).

- [ ] **Step 1: Add a `perf` job**

```yaml
perf:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v7
    - uses: actions/setup-python@v7
      with:
        python-version: "3.12"
    - name: Install Hayavega and benchmark dependencies
      run: pip install -e ".[dev]" waitress
    - name: Run the performance-regression gate
      run: python benchmarks/perf_gate.py
```

Separate job, matching this repo's one-job-per-concern pattern (spec Phase C deliverable) — not folded into `lint`/`typecheck`/`test`.

- [ ] **Step 2: Push and verify it runs and passes on the current baseline**

Run: push to a branch, `gh run view` the `perf` job.
Expected: job passes; printed output shows both `json_echo` and `db_list` measurements.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add a performance-regression gate job"
```

### Task 11: Document the baseline-refresh workflow

**Files:**
- Modify: `benchmarks/README.md`

**Interfaces:** None — documentation only.

- [ ] **Step 1: Add a "Performance regression gate" section to `benchmarks/README.md`**

Document: what it checks (`json_echo`/`db_list`, Hayavega WSGI only, 15% threshold), that CI runs it on every PR via the `perf` job, and the exact refresh command (`python benchmarks/perf_gate.py --refresh-baseline`) with the explicit instruction that a maintainer runs this deliberately after confirming a throughput change is real and intended — never as a way to silence a failing gate — matching the spec's "never refreshed silently" requirement.

- [ ] **Step 2: Commit**

```bash
git add benchmarks/README.md
git commit -m "docs: document the performance-regression gate and baseline-refresh workflow"
```
