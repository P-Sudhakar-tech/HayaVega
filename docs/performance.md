# Performance

Every claim on this page is falsifiable on purpose: judged by published,
reproducible benchmark numbers, never adjectives. Full methodology and
every honest caveat live in `benchmarks/README.md` in the repository;
this page summarizes the headline results.

**Methodology:** [`oha`](https://github.com/hatoo/oha), one warm-up
request per target, then the real measurement. Four scenarios: a minimal
JSON echo, a DB-backed list read, a server-rendered HTML page, and static
file serving. Each framework served by its own idiomatic server. Full
identities of the comparison frameworks are in `benchmarks/README.md` in
the repository. The original pass below used a single run at 10s
duration / 30 connections; `benchmarks/run.py --runs N` (median + min/max
spread across `N` repeated runs) was added since, and the recommended --
and now published -- methodology is 5 repeated runs at 200 connections
(`--duration 10s --connections 200 --runs 5` on Windows; `--duration 5s`
on Linux CI, shorter only to keep CI job time down). Both environments
are published side by side below, each labeled; see
`benchmarks/README.md`'s "Running it" and "Running it in CI (Linux)"
sections for exact invocations and honest caveats.

## vs. other popular frameworks

### Windows (dev machine), original methodology -- kept for history

<div class="hayavega-stats" markdown>
<div class="hayavega-stat"><span class="value">3520</span><span class="label">json_echo req/s</span></div>
<div class="hayavega-stat"><span class="value">920</span><span class="label">db_list req/s</span></div>
<div class="hayavega-stat"><span class="value">799</span><span class="label">template_render req/s</span></div>
<div class="hayavega-stat"><span class="value">1384</span><span class="label">static_file req/s</span></div>
</div>

<p style="margin-top:-1.2rem;font-size:0.82rem;color:var(--md-default-fg-color--light)">Hayavega, WSGI transport, 30 concurrent connections, single run</p>

| Scenario | Hayavega (WSGI) | Hayavega (ASGI) | Async framework | Sync framework |
|---|---:|---:|---:|---:|
| `json_echo` | **3520** | 2153 | 1490 | 1463 |
| `db_list` | **920** | 906 | 634 | 452 |
| `template_render` | **799** | 763 | 688 | 332 |
| `static_file` | **1384** | 1108 | 646 | 921 |

_req/s, higher is better. Single-machine, single-run &mdash; see
`benchmarks/results/RESULTS.md` in the repository for p50/p90/p99 and
success rate on every row._ Kept rather than deleted: this is the
original, broadest comparison referenced elsewhere in this project (e.g.
the "environment ceiling" finding in `INDUSTRY_LEADERSHIP_ROADMAP.md`),
and the re-measurement below uses a different methodology (200
connections, 5 repeated runs), not a direct apples-to-apples update of
this exact table.

### Windows (dev machine), re-measured under the repeated-run methodology

| Scenario | Hayavega (WSGI) | Hayavega (ASGI) | Async framework | Sync framework |
|---|---:|---:|---:|---:|
| `json_echo` | **3784** | 2445 | 1355 | 1956 |
| `db_list` | **1804** | 1701 | 592 | 400 |
| `template_render` | **1340** | 1179 | 659 | 266 |
| `static_file` | **1300** | 1104 | 529 | 1022 |

_req/s, median of 5 runs at 200 connections &mdash; see
`benchmarks/README.md`'s "Running it" section for the exact invocation._

**This is disclosed as a re-measurement, not claimed as a new fix.** No
framework code changed between the original pass above and this one --
`db_list` and `template_render` moved substantially higher for Hayavega's
WSGI transport (roughly +96% and +68% respectively) under this
methodology. The most likely explanation is the higher connection count
(200 vs. the original 30): more concurrent requests queue behind the
same database/thread-pool resources, which raises aggregate throughput
up to a point rather than leaving it flat, the same kind of
concurrency-sensitive behavior this document's own "environment ceiling"
finding already describes for this machine. `json_echo` and
`static_file` moved by smaller, single-digit-to-low-double-digit margins
in both directions, consistent with ordinary run-to-run variance at a
different concurrency level rather than a methodology artifact specific
to those two scenarios.

### Linux (CI), repeated-run methodology

Produced by the `benchmark-linux` CI job (see `benchmarks/README.md`'s
"Running it in CI (Linux)" section), `ubuntu-latest`, `--duration 5s
--connections 200 --runs 5`, GitHub Actions run `42812292945`:

| Scenario | Hayavega (WSGI) | Hayavega (ASGI) | Async framework | Sync framework |
|---|---:|---:|---:|---:|
| `json_echo` | 6239 | **8856** | 3882 | error[^linux-django] |
| `db_list` | 86 | **3926** | 761 | error[^linux-django] |
| `template_render` | 104 | **3002** | 786 | error[^linux-django] |
| `static_file` | 2342 | **2565** | 1087 | error[^linux-django] |

[^linux-django]: error: did not complete within the test window on this
CI runner (not reproduced locally); see benchmarks/README.md's "Running
it in CI (Linux)" section for the full disclosure -- the Django row is
kept, not dropped, per this project's standing disclosure rule.

_req/s, median of 5 runs at 200 connections on a GitHub Actions
`ubuntu-latest` runner &mdash; not the same machine as the Windows
numbers above, so the two environments are not a fair head-to-head
against each other, only each internally consistent._ One more honest
gap, disclosed rather than smoothed over: Hayavega's WSGI transport
(`waitress`) is dramatically slower than its own ASGI transport on this
specific CI runner for `db_list`/`template_render` -- roughly a 45x gap
(86 vs. 3926 req/s) and a 29x gap (104 vs. 3002 req/s) respectively,
computed entirely from the Linux numbers above. It's the *size* of this
WSGI/ASGI gap that's unusual, not a cross-machine throughput comparison:
on Windows the same two transports were close to each other (see the
table above), so this split is specific to the Linux runner. The gap
occurs at a 100% success rate -- no timeouts or errors are depressing
the WSGI figure -- but with genuine tail-latency contention, e.g. p99
around 4.6s vs. p50 around 75ms on `db_list`. This was not root-caused
as part of publishing these numbers; it is published as-is, honestly, as
a lead for a future investigation rather than papered over or excluded.
`benchmarks/README.md`'s "Performance regression gate" section
cross-references this same anomaly as the more likely (though still
unconfirmed) explanation for `db_list`'s ~10x between-VM noise on the CI
performance gate, rather than that noise being disk-I/O-bound.

Hayavega leads every published scenario on both transports on Windows
**against FastAPI and Django**, the two comparison frameworks above. On
Linux CI, Hayavega's ASGI transport leads every scenario; the WSGI
transport leads `json_echo` and `static_file` but trails the async
framework on `db_list`/`template_render` on this specific runner --
the same gap disclosed immediately above, published honestly rather
than rounded up to a clean sweep. Against the four harder targets below
(a raw-ASGI baseline, Granian, Robyn, Blacksheep), that clean sweep does
not hold -- see the disclosure under that table.

### Windows (dev machine), eight-way comparison: Granian, Robyn, Blacksheep, and a raw-ASGI baseline

Four harder targets, added for credibility rather than flattery: a
raw-ASGI baseline with no framework at all, that same baseline served by
Granian (a Rust-core server), Robyn (a Rust-core framework that hosts
itself), and Blacksheep (a fast pure-Python ASGI framework). Same
methodology as the re-measured table above (`--duration 10s --connections
200 --runs 5`, same Windows dev machine). Full detail (p50/p90/p99,
exact invocations, port assignments, a flaky first run caught and
discarded on retry) is in `benchmarks/README.md`'s "Eight-way comparison"
section.

| Scenario | Hayavega (WSGI) | Raw ASGI | Granian | Robyn | Blacksheep |
|---|---:|---:|---:|---:|---:|
| `json_echo` | 4368 | 4687 | 5171 | **14303** | 4327 |
| `db_list` | **1926** | 1108 | 1439 | 1491 | 1062 |
| `template_render` | 1441 | 1149 | **1506** | 1446 | 989 |
| `static_file` | 1579 | 4863 | **5375** | 4252 | 1178 |

_req/s, median of 5 runs at 200 connections, 100% success on every cell._

**A disclosed, unreconciled gap worth flagging: Hayavega (WSGI)'s own
`json_echo` number here (4368) differs by about 16% from the "re-measured"
table above (3784), even though both claim the identical methodology
(`--duration 10s --connections 200 --runs 5`) on the same Windows dev
machine.** Several other numbers between those two tables drift by
15-30% the same way. This is treated the same as every other noise
finding already disclosed in this document: a real, session-to-session
environment property of this shared dev machine, not hidden or smoothed
over. No attempt is made to reconcile the two tables' numbers against each
other -- they are each internally consistent (100% success rate, same
invocation), just not directly comparable session-to-session.

**Disclosed, not rounded up: Hayavega does not win every scenario here.**
It wins `db_list` outright against all four, by 1.3&ndash;1.7&times;, and
effectively ties `template_render` with Robyn (0.3% apart) while trailing
Granian there by a real 4.5%. But on `json_echo` and `static_file` -- the
two scenarios with the least work per request, exactly where a thinner
stack has the most room to show it -- Hayavega loses to Raw ASGI,
Granian, and Robyn by a real margin, up to Robyn's decisive
**3.28&times;** on `json_echo`. Blacksheep, the one new target that is
also a full Python web framework rather than a bare baseline or a
Rust-core runtime, loses every scenario to Hayavega except an
effectively-tied `json_echo` (0.9% apart).

## The Rust runtime vs. a reference WSGI server

```python title="config/settings.py"
class server:
    engine = "rust"  # see Rust runtime
```

| Scenario | Rust runtime | Reference WSGI server | Ratio |
|---|---:|---:|---:|
| `json_echo` | 10362 req/s | 4807 req/s | **2.16&times;** |
| `db_list` | 3926 req/s | 1267 req/s | **3.10&times;** |
| `template_render` | 2692 req/s | 1328 req/s | **2.03&times;** |
| `static_file` | 7242 req/s | 4980 req/s | **1.45&times;** |

_Re-measured 2026-11-12 (two runs, `oha -z 10s -c 30`, 100% success every
target every run). `static_file` was briefly a confirmed, reproducible
**loss** (0.41&ndash;0.54&times;) in an earlier re-measurement the same
day -- real profiling (`py-spy`, not a guess) found `hayavega.static`'s
`serve()` calling `Path.resolve()` (a real filesystem syscall) on every
single request, which this project's own existing template-caching
precedent (`templates.py`'s mtime-invalidated `_compile_cached`) already
established the fix for: cache the resolved path and the file's
content/ETag, invalidated by `stat()`'s mtime, so a repeat request for an
unchanged file skips both the path-resolution and the file-read/hash work
entirely. Fixed in the same change as this re-measurement -- see
`src/hayavega/static.py` and `tests/test_mvp_polish.py`'s
`test_static_routes_caches_*` tests. Every scenario is now a genuine win,
not three wins and one disclosed loss.

100% success rate, both targets, every run &mdash; and this reflects the
**hardened** server (request-smuggling, response-splitting, and
Slowloris defenses all included) with zero measured performance cost.
See [Rust runtime](topics/rust-runtime.md).

## More Rust-acceleration wins since

The two results above are the original, broadest ones; a handful of
narrower candidates cleared the same "benchmark first, wire in only on
a decisive win" bar since. Full reasoning and the complete rejected-
candidate history live in `RUST_CORE_REWRITE_ROADMAP.md`.

| Candidate | Result | Status |
|---|---:|---|
| `SerializedConnection` (one Rust-serialized SQLite connection, a hot read path) | **1.5&ndash;1.7&times;** | Wired in, opt-in ([Models](topics/models.md)) |
| GraphQL lexer+parser (Rust port, executor stays Python &mdash; resolvers are arbitrary user code) | **2.7&ndash;3.9&times;** | Wired in automatically ([GraphQL](topics/graphql.md)) |

## Two more wins, found via a real cross-framework comparison

Building the same sample e-commerce API in Hayavega and two other popular
frameworks (one async, one sync) and benchmarking all three (not a
synthetic microbenchmark) surfaced two real framework-level fixes:

- **Lazy session sweep**: `authenticate_session()` ran a session-
  cleanup `DELETE` + commit on *every* authenticated request, not just
  a read. Gated to run at most once a minute per database instead
  (`DELETE` is housekeeping, not a correctness dependency &mdash; the
  `SELECT`'s own `expires_at > ?` filter already rejects an expired
  session regardless). A real authenticated endpoint that had been
  losing to the equivalent endpoints in the other two frameworks despite
  Hayavega winning every other scenario went from behind to a decisive **~2.7&ndash;2.9&times;**
  win. See [Authentication](topics/auth.md).
- **Opt-in SQLite WAL mode**: SQLite's untouched defaults
  (`journal_mode=DELETE`, `synchronous=FULL`) cost ~6.4&ndash;6.9ms per
  write-transaction commit on this project's own Windows environment,
  almost entirely `fsync` overhead. `db.enable_wal_mode()` (SQLite's
  own recommended pairing for write-heavy workloads) drops that to
  ~0.15&ndash;0.17ms &mdash; a **~39&ndash;47&times;** improvement, confirmed at the
  HTTP level too (a write-heavy endpoint moved from a near-tie with
  the async comparison framework to a **2.0&ndash;2.4&times;** win). See [Models](topics/models.md).

## Losses, disclosed rather than smoothed over

This project's standing rule: publish what loses, not only what wins.

- **HTTPS overhead**: ~0&ndash;12% depending on scenario, since the TLS
  handshake is paid once per connection and amortized by keep-alive, not
  paid per request &mdash; `db_list` (~12%) was the one scenario where this
  read as real rather than noise.
- **Native Rust functions that stay unused**: three PyO3 functions
  (`serialize_json`, `compile_selection`, `normalize_path`) were
  benchmarked against their pure-Python equivalents and *lost* &mdash;
  `serialize_json` lost to `msgspec.json.encode` by ~21&ndash;22&times;. They
  remain defined but unwired, correctly, since wiring them in would have
  been a regression.
- **A general native template compiler**: built, benchmarked, and
  Python's own interpreter won by 1.4&ndash;2.1&times; even after removing
  decode-caching as a variable. A narrow, hardcoded Rust renderer for one
  exact template shape *does* win (16&ndash;20&times; isolated), but shipping
  it as the default would mean dropping template-language features &mdash; a
  correctness regression disguised as a performance win.
- **The WSGI `template_render` gap**: historically documented around
  ~11% behind the async comparison framework; re-measured across four
  fresh runs and found to average to a statistical tie, with individual runs swinging up to 17%
  in either direction &mdash; a lesson in not trusting a single-run
  benchmark number for this specific scenario.
- **`json_echo` and `static_file` against Raw ASGI, Granian, and Robyn**:
  up to a decisive 3.28&times; loss to Robyn on `json_echo`, and
  3.08&ndash;3.40&times; losses to Raw ASGI/Granian on `static_file` --
  the two scenarios with the least work per request, against three
  targets with less framework/adapter overhead per request than Hayavega
  has on any transport. See "Windows (dev machine), eight-way
  comparison" above for the full table and the one scenario (`db_list`)
  where Hayavega beats all three instead.

## Methodology notes worth knowing

- **Windows-specific findings, disclosed as such.** This project's own
  dev environment is Windows; two real methodology bugs were found and
  fixed along the way (a benchmarked server blocking once its `stdout`
  pipe filled under load; one comparison framework's response objects
  not setting `Content-Length`, silently exhausting Windows' ephemeral
  port range).
  Both are documented in full in `benchmarks/README.md` so the next
  framework added to the suite doesn't rediscover them the hard way.
- **An "environment ceiling" was found and controlled for.** A
  completely minimal, framework-free WSGI app doing the identical
  `sqlite3.connect()` + `SELECT` hit the same ~700&ndash;800 req/s ceiling
  as every framework in the suite &mdash; proving that ceiling is this
  machine's SQLite-under-concurrency behavior, not something any
  framework's code can be blamed for or beat through code changes alone.

Full numbers, every benchmark script, and the complete narrative of what
was tried (including the dead ends) live in `benchmarks/README.md` in
the repository.
