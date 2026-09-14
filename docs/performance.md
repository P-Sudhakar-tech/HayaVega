# Performance

Every claim on this page is falsifiable on purpose: judged by published,
reproducible benchmark numbers, never adjectives. Full methodology and
every honest caveat live in `benchmarks/README.md` in the repository;
this page summarizes the headline results.

**Methodology:** [`oha`](https://github.com/hatoo/oha), 10s duration, 30
concurrent connections, one warm-up request per target. Four scenarios:
a minimal JSON echo, a DB-backed list read, a server-rendered HTML page,
and static file serving. Each framework served by its own idiomatic
server.

## vs. FastAPI and Django

<div class="mangoo-stats" markdown>
<div class="mangoo-stat"><span class="value">3520</span><span class="label">json_echo req/s</span></div>
<div class="mangoo-stat"><span class="value">920</span><span class="label">db_list req/s</span></div>
<div class="mangoo-stat"><span class="value">799</span><span class="label">template_render req/s</span></div>
<div class="mangoo-stat"><span class="value">1384</span><span class="label">static_file req/s</span></div>
</div>

<p style="margin-top:-1.2rem;font-size:0.82rem;color:var(--md-default-fg-color--light)">Mangoo, WSGI transport, 30 concurrent connections</p>

| Scenario | Mangoo (WSGI) | Mangoo (ASGI) | FastAPI | Django |
|---|---:|---:|---:|---:|
| `json_echo` | **3520** | 2153 | 1490 | 1463 |
| `db_list` | **920** | 906 | 634 | 452 |
| `template_render` | **799** | 763 | 688 | 332 |
| `static_file` | **1384** | 1108 | 646 | 921 |

_req/s, higher is better. Single-machine, single-run &mdash; see
`benchmarks/results/RESULTS.md` in the repository for p50/p90/p99 and
success rate on every row._

Mangoo leads every published scenario on both transports.

## The Rust runtime vs. waitress

```python title="project/settings.py"
SERVER = {"ENGINE": "rust"}  # see Rust runtime
```

| Scenario | Rust runtime | waitress | Ratio |
|---|---:|---:|---:|
| `json_echo` | 8642 req/s | 3488 req/s | **2.48&times;** |
| `db_list` | 1486 req/s | 827 req/s | **1.80&times;** |
| `template_render` | 1102 req/s | 712 req/s | **1.55&times;** |
| `static_file` | 1939 req/s | 1210 req/s | **1.60&times;** |

100% success rate, both targets, every run &mdash; and this reflects the
**hardened** server (request-smuggling, response-splitting, and
Slowloris defenses all included) with zero measured performance cost.
See [Rust runtime](topics/rust-runtime.md).

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
  ~11% behind FastAPI; re-measured across four fresh runs and found to
  average to a statistical tie, with individual runs swinging up to 17%
  in either direction &mdash; a lesson in not trusting a single-run
  benchmark number for this specific scenario.

## Methodology notes worth knowing

- **Windows-specific findings, disclosed as such.** This project's own
  dev environment is Windows; two real methodology bugs were found and
  fixed along the way (a benchmarked server blocking once its `stdout`
  pipe filled under load; Django's response objects not setting
  `Content-Length`, silently exhausting Windows' ephemeral port range).
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
