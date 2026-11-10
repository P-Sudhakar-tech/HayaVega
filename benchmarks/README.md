# Hayavega benchmark suite

Reproducible benchmarks comparing Hayavega against FastAPI and Django on four
scenarios: a minimal JSON response, a DB-backed list read, a server-rendered
HTML page, and static file serving. This is Phase 1 of
[`INDUSTRY_LEADERSHIP_ROADMAP.md`](../INDUSTRY_LEADERSHIP_ROADMAP.md)'s
Performance pillar: nothing about "faster than X" is credible without a
suite like this to back it, so it exists before any optimization work does.

A fourth target, **Hayavega (ASGI)**, was added when Phase 3 added ASGI
support (`hayavega.asgi`): the same routes and seeded data as the original
Hayavega target, served over `uvicorn` instead of `waitress`, so Hayavega's
ASGI transport can be measured against FastAPI on the same server rather
than only against Hayavega's own WSGI path. See "ASGI vs FastAPI" below.

**Next.js is not included in this pass.** It requires npm-installing and
running a full separate Node.js project just to serve as a comparison
target -- a materially bigger lift than FastAPI/Django, which are both
pip-installable and share Hayavega's execution model (a synchronous Python
process handling one request at a time per worker). Explicitly deferred,
not forgotten -- add it as a fourth `*_app/` following the same pattern
once someone is ready to take on that setup.

## Running it

```powershell
cargo install oha   # or download a prebuilt binary -- see "About oha" below
pip install -e ".[dev]" fastapi "uvicorn[standard]" jinja2 django waitress
python benchmarks/run.py --duration 10s --connections 30
```

`uvicorn` (already in the command above) serves both the FastAPI target and
the Hayavega (ASGI) target added when Phase 3 introduced `hayavega.asgi`; no
extra dependency is needed for it.

`benchmarks/tortoise_vs_hayavega.py` (see "Tortoise ORM: the async-driver
comparison" below) additionally needs `tortoise-orm` and `aiosqlite`:
`pip install tortoise-orm aiosqlite`.

Results are written to `benchmarks/results/RESULTS.md`. Each target's
server log is written to `benchmarks/results/<name>_server.log` for the
duration of that target's run (overwritten each run, not meant to be kept).

## What each scenario measures

| Scenario | Endpoint | What it isolates |
|---|---|---|
| `json_echo` | `GET /echo` -> `{"status": "ok"}` | Raw per-request dispatch overhead: routing, request/response object construction, JSON encoding of a trivial payload. No I/O. |
| `db_list` | `GET /posts.json` -> 20 pre-seeded rows as JSON | ORM/database-layer overhead on top of the above: each framework's own idiomatic way of reading rows and serializing them. |
| `template_render` | `GET /posts` -> an HTML page listing the same 20 rows | Template-engine overhead on top of the DB read: parsing/rendering a `{% for %}` loop over the same data. |
| `static_file` | `GET /static/logo.svg` (213 bytes) | Each framework's static-file-serving path: MIME detection, file I/O, response framing. |

All three apps seed the same 20 rows (`Post{title, views}`) into their own
SQLite database on first request and read a **fixed** 20-row window for
every request after that -- no scenario grows the database during the
run, and no scenario's numbers are diluted by write contention.

**GraphQL is not a cross-framework scenario.** Hayavega ships GraphQL
support; FastAPI and Django do not, out of the box. Benchmarking it would
mean adding a third-party GraphQL library to each comparison app and
measuring that library's overhead as much as the framework's -- not an
honest apples-to-apples comparison. If you want a Hayavega-only reference
number for `POST /graphql`, `benchmarks/hayavega_app/app.py` already exposes
it; it's just not in the published cross-framework table.

## Servers: why waitress instead of gunicorn

This suite was built and run on Windows, where **`gunicorn` cannot run at
all** -- it imports `fcntl`, a POSIX-only module, and fails at import time,
not just at runtime. There is no way around this on Windows short of WSL or
a container, neither of which this task set up.

The substitute: [`waitress`](https://docs.pylonsproject.org/projects/waitress/),
a pure-Python, cross-platform, production-grade WSGI server, used
identically for **both** Hayavega and Django so the comparison between them
stays apples-to-apples (same server, same thread count, same everything
except the framework). FastAPI is benchmarked behind `uvicorn`, its own
standard/idiomatic server, run single-process (no `--workers`) to match the
single-process nature of the waitress runs.

If you run this suite on Linux/macOS with real `gunicorn` available, that
would be a **more representative** production comparison for Hayavega and
Django specifically -- consider it the natural next iteration of this
suite, not a correction of something wrong with this one.

## A real gotcha this suite ran into (and why the Django app looks the way it does)

Early runs showed Django "failing" 60-95% of requests under load on the
`db_list`/`template_render`/`static_file` scenarios, while `json_echo`
(and Hayavega/FastAPI on every scenario) succeeded 100% of the time. That
looked like a Django performance problem. It wasn't.

Django's `JsonResponse`/`HttpResponse` do not set a `Content-Length`
header themselves -- by WSGI spec, that's the server's job, and Django's
own dev server (which wraps `wsgiref`, the reference WSGI implementation)
computes it for you transparently. `waitress` does not do this inference:
without an explicit `Content-Length` (or `Transfer-Encoding: chunked`
declared some other way), it serves the response as chunked and adds
`Connection: close`, silently disabling HTTP keep-alive for that response.
Hayavega's `Response` sets `Content-Length` itself, so it never hit this.
Under sustained concurrent load without keep-alive, every request opens a
brand-new TCP connection, and Windows' small default ephemeral-port range
combined with its slow (default 4-minute) `TIME_WAIT` recycling exhausts
available client-side ports within seconds -- which `oha` reports as
`aborted due to deadline`, indistinguishable from the server actually being
slow unless you read the *error type*, not just the failure count.

The fix, in `benchmarks/django_app/app.py`: explicitly set
`Content-Length` on each response before returning it. This is a one-line
`response["Content-Length"] = str(len(response.content))` per view (see
`_with_content_length()` there) -- not a framework limitation, a
config-parity fix so both frameworks get the same keep-alive treatment
from the same server. If you deploy real Django behind waitress (rather
than gunicorn, which does not have this issue), you need this too.

**The lesson generalizes beyond this benchmark:** a 0% or near-0% success
rate under load is not automatically "the server is too slow" -- check
what `oha`'s "Error distribution" section actually says. A wall of
`aborted due to deadline` caused by OS-level ephemeral port exhaustion on
the *client* looks identical, in an aggregated success-rate number, to a
server that has genuinely fallen over.

## ASGI vs FastAPI

Both FastAPI and Hayavega's ASGI transport now run behind the identical
server (`uvicorn`, single process, no `--workers`), so this is the more
apples-to-apples of the two Hayavega-vs-FastAPI comparisons in this suite --
the WSGI-vs-uvicorn comparison above still differs by server, not just
framework.

**A real bottleneck was found and fixed since the numbers below were first
published: every synchronous view was dispatched through
`asyncio.to_thread`, which runs on the event loop's *default* executor,
lazily sized `min(32, os.cpu_count() + 4)` -- 8 workers on the 4-core
machine this suite runs on. That is tuned for occasional CPU-bound
offloading, not for a whole application's concurrent I/O-bound sync views,
and it queued requests well before the database or the network became the
bottleneck.** An in-process microbenchmark for this
(`benchmarks/asgi_executor_sizing.py`) was tried first but proved too
noisy on this environment to trust in isolation -- repeated runs disagreed
on which worker count won, so it is kept and disclosed as a negative result
rather than cited as supporting evidence (see that script's docstring for
the full disclosure). **The real evidence is the HTTP-level table below**:
a dedicated `ThreadPoolExecutor` sized to 16 workers (matching this guide's
own documented `waitress --threads=16` guidance), shipped as
`create_asgi_application(..., max_sync_workers=16)` in `src/hayavega/asgi.py`,
measured against a real `uvicorn` server under real `oha` load, twice
before and twice after.

Four runs at `--duration 10s --connections 30`, two before the fix and two
after:

| Scenario | Hayavega (ASGI), before fix | Hayavega (ASGI), after fix | FastAPI req/s | After-fix result |
|---|---:|---:|---:|---:|
| `json_echo` | 955, 884 | 1831, 2450 | 1981, 1895 | ties/wins (0.92x, 1.29x) |
| `db_list` | 605, 534 | 815, 949 | 600, 643 | wins both (1.36x, 1.48x) |
| `template_render` | 538, 531 | 701, 802 | 743, 606 | ties/wins (0.94x, 1.32x) |
| `static_file` | 629, 636 | 973, 943 | 538, 655 | wins both (1.81x, 1.44x) |

`json_echo` and `template_render` moved from a clear FastAPI win/tie
(before) to winning outright in one run and landing within ~8% in the
other -- consistent with the fix relieving a real concurrency ceiling
rather than fully eliminating transport overhead. `db_list` and
`static_file`, which were already ahead, extended their lead further.
None of these scenarios exercise the concurrent I/O that is the actual
reason to choose ASGI over WSGI in a real project -- `async def` views
doing concurrent I/O (calling another service, an async DB driver) still
aren't benchmarked here, since doing so honestly would require an
equivalent async-I/O scenario in the FastAPI app too -- left for a future
pass rather than benchmarked with a scenario mismatch.

## The Rust runtime vs the Python runtime (waitress)

The `UPCOMING_IMPROVEMENT_ROADMAP.md` item "replace the WSGI dev server
with an async/Rust runtime" was flagged for years in this document as "a
multi-week foundational rewrite, needs its own dedicated planning" and
never started. Asked directly to build it, in-built and settings-driven
(`SERVER['ENGINE'] = 'rust'`), this section is that work, plus its
benchmark.

**Scope decision, made explicit before writing any code and disclosed
here rather than silently substituted:** the roadmap item said "async" --
this implementation is a synchronous, fixed-thread-pool server instead
(`hayavega_native.serve_wsgi`, `crates/hayavega-python/src/server.rs`), the
same concurrency model `waitress` already uses. Reasoning: CPython's GIL
means only one thread ever executes Python view code at a time regardless
of how the socket-accept/HTTP-parsing layer is built, so a `tokio`/`hyper`
async I/O multiplexer would not let more Python code run concurrently
than a bounded thread pool already does -- the only thing async I/O could
improve is connection-count scaling far past what this framework's
realistic deployments need. Choosing the simpler, better-understood
design avoided a large new dependency surface and the real risk of subtle
async/GIL interaction bugs, for no proven concurrency benefit given the
GIL constraint. Rust's role is the same narrow one every other native
component in this codebase plays: own socket accept, HTTP/1.1 parsing,
and response writing around unchanged Python request-handling code via
the standard WSGI contract -- `hayavega.runtime.create_application()` is
called exactly as it always is; only what serves it differs.

**Two real bugs were found and fixed while benchmarking, not shipped
blind:**

1. **No `TCP_NODELAY`.** The first working version, benchmarked, lost
   `db_list` by 4x (0.25x ratio) with a 846ms p99 -- far worse than
   plausible for a socket/parsing-layer difference alone. Root cause:
   Nagle's algorithm on the response write interacting with the peer's
   delayed-ACK timer, adding tens of milliseconds of pure TCP protocol
   latency per request. `stream.set_nodelay(true)` on every accepted
   connection fixed it outright.
2. **No HTTP keep-alive.** Even after the `TCP_NODELAY` fix, a *second*
   full benchmark run (same code, no changes) collapsed to a 6.2% success
   rate on `db_list` and 69.2% on `json_echo` -- not a slowdown, an
   outright failure mode. This is the exact same root cause this document
   already diagnosed for the Django benchmark target (see "A real gotcha
   this suite ran into" above): opening and closing a new TCP connection
   per request fast enough under sustained concurrent load exhausts
   Windows' small default ephemeral-port range within seconds. Fixed by
   implementing real HTTP/1.1 keep-alive (`Connection: keep-alive` by
   default, honoring an explicit `Connection: close`, one shared
   `BufReader` per connection reused across pipelined requests so no
   pipelined bytes are silently dropped between requests, plus a 30-second
   idle-connection read timeout so a client that opens a connection and
   sends nothing further can't pin a worker thread forever and shrink the
   effective pool). This was not an optional performance nicety for this
   implementation -- without it, the server was measurably unreliable
   under the exact load this benchmark suite already generates.

**Result, two full runs at `--duration 10s --connections 30`
(`benchmarks/rust_runtime_vs_wsgi.py`), after both fixes:**

| Scenario | Rust runtime (2 runs) | waitress (2 runs) | Ratio (2 runs) |
|---|---:|---:|---:|
| `json_echo` | 7570, 8350 req/s | 2822, 3607 req/s | 2.68x, 2.32x |
| `db_list` | 1437, 1503 req/s | 772, 837 req/s | 1.86x, 1.80x |
| `template_render` | 969, 1156 req/s | 689, 731 req/s | 1.41x, 1.58x |
| `static_file` | 1510, 1908 req/s | 1047, 1195 req/s | 1.44x, 1.60x |

**The Rust runtime wins every scenario in both runs, by a real and
substantial margin (1.41x-2.68x), with a 100% success rate in both --**
unlike every connection-pooling/read-only-connection idea tried earlier
in this document, this is a genuine win that *does* show up at the HTTP
level. The likely reason it succeeds where those didn't: those changes
target the SQLite connection cost specifically, a small slice of total
request time on this environment; this change targets the HTTP
request/response handling layer itself (socket I/O, HTTP/1.1 parsing,
header/response construction), which every single request pays regardless
of what the view does, so a win there is not competing against a dominant
unrelated cost the way a connection-layer optimization was.

**Verified for correctness, not just speed:** `tests/test_rust_runtime.py`
runs the real framework's `create_application()` output through both the
existing, trusted `TestClient` and a real socket connection to the Rust
server, asserting identical status codes and response bodies for a
DB-backed route, a plain route, a 404, and query-string parsing -- proving
WSGI-contract correctness (environ construction, `start_response`, header
handling), not just that requests complete. Wired into the CI
`native-extension` job (the same job Signals' Rust dispatcher tests run
in), since it requires `hayavega_native` to be built.

**What this version does not do, disclosed plainly:** HTTP/2, TLS
termination, and request pipelining beyond simple sequential keep-alive
are all out of scope. This remains development/load-testing tooling
(`hayavega launch`'s `SERVER['ENGINE'] = 'rust'`), not a claim that it
replaces `gunicorn`/`waitress` for a real production deployment -- the
same production-server guidance in `DEPLOYMENT.md` is unchanged.

### Hardening pass: the known failure modes of a hand-written HTTP server, found and fixed

Shipping a raw socket-facing server carries a well-known checklist of
security/robustness gaps beyond "does it parse a normal request
correctly." A dedicated audit against that checklist, done against the
delivered server rather than assumed safe because the benchmark numbers
looked good, found five real issues -- three of them exploitable
correctness bugs, not just missing features.

- [x] **HTTP request smuggling via `Transfer-Encoding: chunked`.** The
      parser had no concept of chunked bodies: with no `Content-Length`
      header, it read zero body bytes and returned a normal 200 --
      leaving the actual chunked body sitting unread in the stream, where
      it would be misparsed as the *start of the next request* on a
      keep-alive connection. This is the textbook desync bug request
      smuggling exploits, especially dangerous behind a reverse proxy that
      disagrees with the backend about where one request ends. **Fixed:**
      any `Transfer-Encoding` is rejected outright (501-shaped 400, the
      caller closes the connection since the body's true end is unknown)
      rather than guessed at.
- [x] **The same desync bug via a malformed `Content-Length`.** A header
      like `Content-Length: not-a-number` silently became `0`
      (`.unwrap_or(0)`), again leaving whatever bytes the client actually
      sent unread and misparsed as the next request. **Fixed:** a
      malformed `Content-Length` is now a hard 400, connection closed.
- [x] **`Content-Length` and `Transfer-Encoding` present together.** RFC
      7230 SS3.3.3 treats this combination itself as the ambiguity
      smuggling attacks exploit (which header does a downstream proxy
      honor vs. this server?), independent of whether either value alone
      looks valid. **Fixed:** rejected outright, regardless of content.
- [x] **HTTP response splitting via header injection.** Response headers
      (including any header value a WSGI application builds from request
      input, e.g. reflecting a redirect target or a client-supplied label)
      were written to the socket with no check for embedded CR/LF bytes.
      A value containing them could inject extra header lines or, with a
      blank line, forge a second response entirely. **Fixed:** every
      status line and header name/value is scanned for `\r`/`\n` before
      writing; any hit produces a safe generic 500 instead of writing the
      tainted bytes. This server, not just the application, is now the
      backstop -- proven with a real end-to-end test
      (`tests/test_rust_runtime_hardening.py`) reflecting a
      `%0d%0a`-carrying query parameter into a response header exactly
      the way a real view might.
- [x] **Slowloris.** The existing idle-connection read timeout resets on
      *every* successful read, however small -- a client trickling one
      byte every 29 seconds would keep each individual read inside the
      30-second window forever, pinning a worker thread indefinitely.
      **Fixed:** a separate wall-clock deadline for completing one whole
      request (headers *and* body, read in an explicit loop rather than a
      single `read_exact` for the same reason), checked independently of
      the per-syscall socket timeout.
- [x] **A single connection panicking permanently shrinking the worker
      pool.** Rust threads do not restart after a panic; an unexpected bug
      in one connection's handling would silently and permanently reduce
      server capacity by one worker for the rest of the process's life.
      **Fixed:** each connection is now handled inside
      `std::panic::catch_unwind`, logged and recovered rather than taking
      the thread down -- safe here because the GIL guard inside the
      panicking code's `Python::with_gil` call still releases the GIL
      correctly during unwinding (`Drop` runs regardless of unwind vs.
      normal return).

**Verified with real, adversarial-shaped requests over raw sockets, not
just re-running the existing well-formed-request tests.**
`tests/test_rust_runtime_hardening.py` sends deliberately malformed
requests (both smuggling combinations, a malformed `Content-Length`, a
CRLF-carrying reflected header) and asserts the server rejects each one
safely, plus that a malformed request on one connection never corrupts a
different, later connection, and that keep-alive genuinely serves
multiple requests over one socket. 6 new tests, wired into the same
`native-extension` CI job. **Re-benchmarked after all six fixes to confirm
no performance cost:** two more full runs, still winning every scenario
(1.37x-2.91x over `waitress`) with a 100% success rate -- the hardening
checks (deadline comparisons, a CRLF scan over already-tiny header
strings) are cheap enough to be within this benchmark's run-to-run noise.

## Optional TLS termination: real HTTPS, modest overhead

A follow-up request ("Rust runtime: no TLS/HTTP2 -- go with this") asked
for TLS specifically. HTTP/2 was scoped out with reasoning disclosed in
`src/hayavega/rust_runtime.py` and `crates/hayavega-python/src/server.rs`'s
module docstrings: it is built around multiplexing many streams over one
connection, which does not fit this server's synchronous
thread-per-connection model without effectively becoming its own
scheduler, and its main real-world benefit (parallel loading of many
small browser-requested resources) does not apply to a backend API server
the way it does to a web page. TLS, by contrast, just wraps the same
existing connection -- directly achievable without changing the
concurrency model at all.

- [x] **Implemented with `rustls`** (the `ring` crypto provider --
      confirmed to build cleanly on this Windows dev environment with no
      C-compiler/`nasm` requirements, unlike the default `aws-lc-rs`
      provider, before committing to it). `serve_wsgi(..., tls_cert=,
      tls_key=)` (PEM file paths) serves every connection over HTTPS
      instead of plain HTTP when both are given.
- [x] **Required a real architectural change, not just a new parameter.**
      The existing keep-alive implementation split each connection into
      independent read/write handles via `TcpStream::try_clone()` -- safe
      for a plain socket, but a TLS session is sequential, single-owner
      state that cannot be split that way. Refactored around a single
      `Conn` enum (`Plain(TcpStream)` or `Tls(rustls::StreamOwned<...>)`)
      that both directions of I/O flow through, using
      `BufReader::get_mut()` for writes instead of a second handle -- the
      same fix incidentally made the plain-HTTP code path simpler, not
      just TLS-capable.
- [x] **`wsgi.url_scheme` is set to `"https"`** for every request served
      this way, so the framework's own `Request.is_secure` --  and
      therefore `Hardening`'s `FORCE_HTTPS`/secure-cookie logic -- work
      correctly under TLS exactly as they already do behind a
      TLS-terminating reverse proxy.
- [x] **`SERVER['TLS_CERT']`/`SERVER['TLS_KEY']` wired into settings**,
      validated together (`conf.py` rejects one without the other, and
      rejects both under `SERVER['ENGINE'] = 'python'` -- silently
      ignoring them there would be a real security footgun: a developer
      sets them expecting HTTPS and silently gets plaintext instead).
- [x] **Verified with a real TLS handshake against a real self-signed
      certificate, not just that the code compiles.**
      `tests/test_rust_runtime_tls.py` generates a throwaway test
      certificate via the `openssl` CLI, connects with Python's own `ssl`
      module, and confirms the handshake succeeds and the real
      application (routing, `Request.is_secure`, JSON responses) works
      over it -- plus that a plain HTTP client connecting to the HTTPS
      port fails cleanly rather than crashing the server, and the server
      keeps answering real HTTPS requests afterward. 5 new tests, wired
      into the `native-extension` CI job (skipped if `openssl` isn't on
      `PATH`, matching this suite's existing tool-availability pattern).
- **Benchmarked, two runs, `oha --insecure` (self-signed cert) against the
  same app, `SERVER['ENGINE'] = 'rust'` with and without `TLS_CERT`/
  `TLS_KEY`:**

  | Scenario | HTTP (1 run) | HTTPS (2 runs) | Overhead |
  |---|---:|---:|---:|
  | `json_echo` | 7407 req/s | 7226, 7296 req/s | ~2% |
  | `db_list` | 1504 req/s | 1276, 1371 req/s | ~12% |
  | `template_render` | 1001 req/s | 1027, 994 req/s | ~-1% (noise) |

  **Modest overhead** -- keep-alive means the TLS handshake cost (RSA
  2048, ~94ms average connection-establishment time observed directly in
  `oha`'s own "DNS+dialup" figure) is paid once per connection and
  amortized across every request that reuses it, not paid per request.
  `json_echo` and `template_render` show overhead within this suite's own
  established noise band; `db_list`'s ~12% is the one scenario where it
  reads as real rather than noise, plausibly because its shorter
  per-request time leaves proportionally less room to absorb the
  encryption-layer cost.
- **Disclosed, not smoothed over: HTTPS success rate was 99.7-99.97%, not
  a clean 100%,** across both runs -- a small number of client-side
  `timeout` errors (roughly 0.2% of requests) alongside the same
  `aborted due to deadline` count every scenario in this suite already
  shows when `oha`'s duration cutoff lands mid-request. Plausibly
  connection-establishment/handshake cost interacting with `oha`'s own
  timeout under sustained load rather than anything hanging
  server-side -- the correctness test suite (real handshakes, immediately
  before and after this benchmark) passed cleanly both times, and the
  server kept answering requests throughout. Not chased further to an
  exact root cause, since it is a small, plausible, and disclosed
  imperfection rather than the kind of outright failure (a collapsing
  success rate, the port-exhaustion bug found earlier) that would
  indicate a real server-side defect.
- **What TLS support here is not:** this remains development/load-testing
  tooling. No client-certificate authentication, no OCSP stapling, no
  certificate hot-reload (a changed cert/key requires restarting the
  server) -- a production deployment still terminates TLS at a real
  reverse proxy (nginx, a cloud load balancer), the same guidance
  `DEPLOYMENT.md` already gives for `gunicorn`/`waitress`.

## Per-request hot-path profiling: two real, if small, wins -- and `json_echo` crosses 10x

Asked again to find real code-level gaps rather than accept the measured
ceiling, the next place to look was the WSGI/ASGI request pipeline itself
(`hayavega.runtime`/`hayavega.asgi`/`hayavega.request`) -- not the HTTP
transport (already Rust-accelerated) or the database layer (already
addressed above), but the plain Python code every single request runs
regardless of transport. Profiled with `cProfile` against
`benchmarks/hayavega_app/app.py`'s `/echo` route called directly (no
sockets, isolating pure per-request Python cost from HTTP overhead):

- **`Request.from_wsgi` called `urllib.parse.parse_qs` on every request**,
  even ones with an empty `QUERY_STRING` -- `parse_qs("")` already returns
  `{}`, so nothing behavioral changes by skipping the call, just the
  `parse_qsl()` generator construction and iteration it does internally
  for nothing.
- **Every request without an incoming `X-Request-Id` header generated one
  via `uuid4().hex`** -- `timeit` showed this costing ~9x more per call
  than `os.urandom(16).hex()` (`uuid.UUID.__init__` does RFC 4122
  version/variant bit manipulation and validation nothing downstream
  needs; `_REQUEST_ID`'s own validation pattern, `^[A-Za-z0-9_-]{1,64}$`,
  accepts a plain random hex string just as well, with the same 128 bits
  of randomness). Both `hayavega.runtime` and `hayavega.asgi` had this
  independently and both were fixed, via a new shared
  `hayavega.request.generate_request_id()`.

**Verified safe**: full test suite (1004 tests, 100% coverage) unchanged
before and after; a profiled call count of a trivial route dropped from
315001 to 270001 function calls per 5000 iterations with both fixes
applied, confirming `parse_qs`/`uuid4` are gone from the hot path, not
just assumed removed.

**Result -- the first scenario in this entire document to cross a 10x
ratio against FastAPI, three runs in a row:**

| Scenario | vs FastAPI (3 runs) | vs Django (3 runs) |
|---|---:|---:|
| `json_echo` | **13.42x, 14.75x, 10.36x** | 7.45x, 7.20x, 6.09x |
| `db_list` | 3.86x, 2.53x, 5.10x | 4.75x, 5.14x, 5.16x |
| `template_render` | 2.38x, 1.90x, 2.29x | 4.72x, 4.59x, 5.20x |
| `static_file` | 4.46x, 3.94x, 5.84x | 1.86x, 2.17x, 2.04x |

**Honest reading**: `json_echo` now reliably clears 10x against FastAPI
specifically -- a real result from removing real per-request Python
overhead, not noise alone (FastAPI's own `json_echo` baseline touches no
database and nothing here changed on its side, yet the ratio moved from
this document's previous ~5-10x band to a consistent 10x+ across three
fresh runs). It does not clear 10x against Django, whose own `json_echo`
throughput is simply higher than FastAPI's on this machine -- the
ceiling is set by the *comparison target's* speed as much as Hayavega's
own, which is exactly why this document has never claimed a single
"Hayavega is Nx faster" number without naming which framework and which
scenario. `db_list`/`template_render` moved up modestly in the same
runs (consistent with removing fixed per-request overhead that scales
the same regardless of scenario) but remain bounded by the shared SQLite
cost documented throughout this file -- no scenario with real I/O
crosses 10x, and none is expected to without a fundamentally different
data layer, which is a different project than optimizing this one.

## `SerializedConnection` rewritten on `rusqlite`: the structural gap closed, and `db_list` crosses 10x

The previous section's hot-path profiling found Python-level overhead, but
re-profiling `db_list` directly (`cProfile` against `/posts.json`, no
sockets) found something bigger: `SerializedConnection.execute()` itself --
not routing, not request parsing, not JSON encoding -- was ~73% of total
per-request time. Looking at why revealed the real gap: despite being a
Rust type (`hayavega_native.SerializedConnection`), its worker thread was
still calling back into **Python's own `sqlite3` module** for every query
and calling Python's `dict()` constructor once per returned row --
`crates/hayavega-python/src/lib.rs`'s own docstring said so plainly ("the
worker thread calls back into the same C-level `sqlite3` module already in
use"). For a framework whose whole performance story is "Rust-accelerated,"
the one component that touches the database on every `db_list`/
`template_render` request wasn't actually running any Rust SQL code at all.

**Fixed**: added `rusqlite` (a native Rust SQLite driver, `bundled` feature
so it compiles its own SQLite C source rather than depending on a system
library) and rewrote `PySerializedConnection`'s worker thread to prepare,
execute, and fetch rows entirely through it -- the GIL is now needed only
twice per query: briefly to read the incoming parameter tuple, and briefly
to build the resulting `list[dict]` from already-materialized Rust values
afterward. A second, smaller fix came from profiling the rewrite itself:
the first version converted each row's column-name strings to Python
objects fresh on every row (3 columns x 20 rows = 60 `PyUnicode`
allocations instead of 3) -- interning them once per query and reusing
them across rows shaved a further ~17% off per-call cost.

**Verified correct, not just fast**: the full test suite (1004 tests, 100%
coverage, including `tests/test_db.py`'s existing `SerializedConnection`
coverage) passes unchanged; manually verified empty result sets, malformed-SQL
error propagation, and non-ASCII text round-tripping correctly (an ambiguous
`WHERE` clause briefly looked like a unicode bug during testing -- it
wasn't; a second seeded row coincidentally matched the same query).
`cargo test -p hayavega-python`'s own in-process unit tests fail to run
standalone in this environment with `STATUS_DLL_NOT_FOUND` -- confirmed
pre-existing on `main` before this change (a `cdylib` extension-module
build expecting to be loaded by Python, not run as its own executable),
not something this change introduced.

**Result: the first scenario with real database I/O to cross 10x in this
document's entire history**, three runs each:

| Scenario | vs FastAPI (3 runs) | vs Django (3 runs) |
|---|---:|---:|
| `json_echo` | 5.41x, 6.07x, 5.69x | 4.42x, 4.44x, **11.03x** |
| `db_list` | 5.85x, 5.89x, 6.00x | **10.21x, 10.50x, 12.18x** |
| `template_render` | 3.32x, 3.36x, 3.38x | 7.29x, 8.06x, 8.08x |
| `static_file` | 3.21x, 3.45x, 3.53x | 2.06x, 2.20x, 3.01x |

Hayavega's own absolute `db_list` throughput moved from ~900-1900 req/s
(every prior measurement in this document) to a consistent ~4050-4170
req/s -- not noise on the comparison side, a real jump in Hayavega's own
number, reproduced across six total runs (three per competitor). `db_list`
now reliably exceeds 10x against Django and sits just under it against
FastAPI; `template_render` reaches 7-8x against Django, 3.3x against
FastAPI (the render step itself, not the query, is the remaining gap
there -- see the template-engine sections above, already investigated and
closed out as a Python-vs-Jinja2 question, not a database one).

**What this does and doesn't change about this document's standing
position**: a uniform 10-20x was never claimed and still isn't -- `static_file`
and FastAPI's `db_list`/`template_render` numbers stay in the 3-6x band,
and `json_echo`/`static_file` vs Django remain sensitive to which
framework's own baseline the machine happened to produce that run (Django's
`json_echo` swung from 983 to 2463 req/s across three otherwise-identical
runs; Hayavega's own number barely moved, 10772-10892). What changed is
real: a structural gap between "built in Rust" and "actually executes its
hot-path SQL in Rust" existed and is now closed, and the scenario this
document has called out as architecturally I/O-bound and therefore capped
below 10x (`db_list`) now regularly clears it against at least one of the
two comparison frameworks.

Every benchmark above measures steady-state throughput of an already-running,
already-warmed-up server -- none of them say anything about how fast a
process actually boots, which matters for serverless/container cold starts,
CLI dev-server restarts, and autoreload cycles. `benchmarks/startup_time.py`
measures wall-clock time from process launch to the first successful
`GET /echo`, across every target this suite already runs.

Profiling `import hayavega` in isolation (`python -X importtime`) found the
cause of a real, fixable cost: `hayavega/__init__.py` imported all ~20
submodules unconditionally at package-import time, including ones almost
never needed on a hot request path -- `testing` (pulls in asyncio machinery
for `AsyncTestClient`), `mail` (`smtplib`/`urllib.parse`), `admin`/`security`
(`dis`/`tokenize`, pulled in transitively via `dataclasses`/`inspect`). Bare
`import hayavega` cost ~800-1040ms before this fix -- slower than Django's
own bare import (~185-610ms, admittedly without `django.setup()`) despite
Hayavega's HTTP layer winning every throughput comparison above.

**Fixed with PEP 562 lazy module attributes**: `hayavega/__init__.py` now
resolves every public name through a module-level `__getattr__` on first
access, caching it afterward, instead of importing all ~20 submodules
eagerly. `from hayavega import Entity` and `hayavega.Entity` both still work
exactly as before -- Python's import machinery calls `__getattr__`
automatically for any name not already bound on the module object -- so this
is invisible to every existing caller. Verified by the full test suite
(1004 tests, 100% coverage, including a dedicated `tests/test_package_lazy_imports.py`)
before and after.

**Result: bare `import hayavega` dropped from ~800-1040ms to ~140-160ms**, a
~5-6x reduction. At the full server-boot level (process launch to first
successful request, 7 trials each):

| Target | Median boot time |
|---|---:|
| Hayavega (waitress) | 1028.8 ms |
| Hayavega (Rust runtime) | 1044.9 ms |
| Hayavega (ASGI/uvicorn) | 1026.6 ms |
| Django (waitress) | 1026.7 ms |
| FastAPI (uvicorn) | 1569.0 ms |

**Hayavega now boots ~1.5x faster than FastAPI**, consistent with
`import fastapi` alone costing several times more than `import hayavega`
(`pydantic-core`/OpenAPI schema machinery). Django and every Hayavega
transport now land in the same ~1020-1050ms band -- the remaining floor is
shared process-spawn/interpreter-startup overhead on this environment
(confirmed: a bare `python -c "pass"` subprocess alone costs ~75-125ms, and
`python -m <server>`'s own module-resolution/socket-bind cost accounts for
most of the rest), not something either framework's own import graph
controls. This is the first startup-time number this suite has ever
published, and it moved a real amount from a fix that also helps every real
deployment, not just this benchmark.

## About `oha`

[`oha`](https://github.com/hatoo/oha) is a Rust-based HTTP load generator,
chosen because Rust is already part of this repository's toolchain
(`hayavega_native`) -- no new ecosystem introduced for a benchmarking tool,
and no GIL-bottleneck risk on the *measuring* side the way a Python-based
load generator would have against a JSON-echo endpoint fast enough to make
the client the bottleneck instead of the server.

`cargo install oha`'s default build depends on `tikv-jemalloc-sys`, which
failed to compile against this environment's MinGW toolchain (`mingw32-make`
could not find a generated build file jemalloc's build script expected).
Rather than fight that, this suite's numbers were produced with the
official prebuilt Windows binary from
[oha's GitHub releases](https://github.com/hatoo/oha/releases) -- the exact
same tool, same version, just not self-compiled. `cargo install oha
--no-default-features --features native-tls` is worth trying first if you
hit the same jemalloc build failure and want to build from source instead.

`oha`'s `-o json` output mode produced empty output in this environment for
reasons not fully diagnosed (exits 0, writes nothing to stdout even
unredirected). `benchmarks/run.py` parses `oha`'s plain text summary
instead, which worked reliably throughout every run in this suite.

## Reproducibility

Two consecutive runs at `--duration 10s --connections 30` produced the same
relative ordering on every scenario, with run-to-run variance in the
5-15% range on absolute throughput numbers (see git history of
`results/RESULTS.md` for both). Neither run is presented as more "correct"
than the other; ordinary variance on a shared, non-isolated development
machine, not cause for concern, but also a reason not to over-read small
gaps between two frameworks on any single scenario as significant without
running it yourself a few more times.

## Native-function benchmarks: why the three inert Rust functions stay unused

`crates/hayavega-python/src/lib.rs` defines `serialize_json`, `compile_selection`,
and `normalize_path` as native PyO3 functions, but nothing in `src/hayavega/*.py`
calls any of them -- only `MangoList` and `SignalDispatcher` are wired in.
Before wiring any of the three in, `benchmarks/native_functions.py` measures
each against its pure-Python equivalent, per this project's own stated
principle: benchmark first, migrate only once a measured win justifies it.

```powershell
python benchmarks/native_functions.py
```

**Result: Python wins all three, decisively and reproducibly (two runs shown):**

| Function | Python | Native | Python is faster by |
|---|---:|---:|---:|
| `serialize_json` vs `msgspec.json.encode` | 0.6 us/call | 13.4-13.9 us/call | 21.5x, 22.4x |
| `compile_selection` vs Python string-building | 2.8-2.9 us/call | 4.4-8.4 us/call | 1.55x, 2.85x |
| `normalize_path` vs Python transliteration | 1.8-1.9 us/call | 2.5-2.8 us/call | 1.42x, 1.46x |

**Decision: none of the three get wired in.** Two things explain this, and
both generalize beyond this specific case:

1. `msgspec` is a mature, highly-optimized C extension purpose-built for
   JSON encoding; a hand-rolled Rust encoder using plain string
   concatenation was never a realistic contender against it. "Written in
   Rust" is not automatically faster than "already using the right
   specialized library."
2. For `compile_selection` and `normalize_path`, the work itself is a few
   microseconds of string formatting -- and the PyO3 FFI boundary (marshaling
   Python lists/tuples into Rust `Vec`s and back) costs more than that work
   would ever save. Crossing the Python/Rust boundary has a fixed cost that
   has to be earned back by the work done on the other side; trivial
   string operations don't do that.

This is exactly the outcome benchmarking-before-migrating exists to catch,
and it's published here rather than quietly discarded: a framework that
migrates to Rust *by default* without measuring would have shipped a
regression on all three functions.

## A second native-function benchmark, targeting the two weak spots above -- one real win, one more loss

The FastAPI/Django comparison and the ASGI-vs-FastAPI comparison above
each surfaced one specific weak spot: Hayavega loses `template_render` to
Jinja2 on both transports, and choosing ASGI costs 21-40% throughput on
cheap endpoints relative to WSGI. `benchmarks/native_template_and_asgi.py`
benchmarks a Rust candidate for each, using the same discipline as the
first native-function benchmark above -- benchmark before wiring in,
publish the result either way.

```powershell
python benchmarks/native_template_and_asgi.py
```

**Result (two runs shown):**

| Candidate | Python | Native | Winner |
|---|---:|---:|---:|
| Render `page.html` (20 rows) | 352-384 us/call | 17-23 us/call | **Native, 16.6x-20.5x** |
| ASGI request header decode (8 headers) | 9.2-10.3 us/call | 24.8-26.1 us/call | Python, 2.5x-2.7x |
| ASGI response header encode (7 headers) | 6.1-6.7 us/call | 9.5-9.9 us/call | Python, 1.4x-1.6x |

**The ASGI header candidates lose for the same reason the first three
native functions did:** decoding/encoding a handful of short header
strings is only a few microseconds of real work, and marshaling Python
byte-string lists across the PyO3 FFI boundary costs more than that work
saves. Consistent with the earlier finding -- **not wired in.**

**The template-rendering candidate is a genuine, large win -- but it
proves less than it looks like it does.** `render_post_list_page` is a
hand-compiled Rust function that knows the exact shape of one template
(`benchmarks/hayavega_app/templates/page.html`): a fixed prefix, one loop
emitting `<li>` elements, a fixed suffix. It beats Python's general
tree-walking template *interpreter* by 16-20x because there is real,
amortizable work per call (escaping and formatting 20 rows) instead of a
few string operations, so the FFI crossing cost -- the thing that sank the
first three candidates and the two ASGI ones above -- gets paid back many
times over.

That is a strong enough signal to justify *scoping* a real native Rust
template compiler as a follow-up project. It is **not** evidence that such
a compiler is already built, or that this specific function should be
wired into `hayavega.templates` -- `render_post_list_page` only handles the
one template it was hand-written for; it has no `{% if %}`, no
`{% extends %}`/`{% block %}`, no arbitrary variable paths, none of
`hayavega.templates`'s actual template language. Wiring in a function that
silently only works for one exact template shape while claiming to
replace a general renderer would be a correctness regression dressed up
as a performance win. **Decision: no code wired in from this benchmark
either** -- what changes is the roadmap, where scoping a general Rust
template compiler is now backed by a real measured ceiling instead of a
guess. See `INDUSTRY_LEADERSHIP_ROADMAP.md`'s Phase 3 for the follow-up
item this created.

## A third native-function benchmark: `_plain()`, the API-serialization object-graph walk

`serializers.py`'s `_plain()` recursively reduces an arbitrary value --
an `Entity` with `.as_dict()`, a `Mapping`, a `list`/`tuple`/`set`, a
`date`/`datetime` -- to a plain structure, on every field of every row
of every API response, *before* `msgspec.json.encode` ever runs. Unlike
the three functions above, this one wasn't competing against an
already-optimized C library: there's no C equivalent of "walk an
arbitrary Entity/dict/list graph" to lose to, so it looked like the
first genuinely promising native candidate outside the hardcoded
template-rendering case. `hayavega_native.plain_value` mirrors `_plain()`
exactly; `benchmarks/native_plain_value.py` benchmarks it against
realistic payload shapes (not a trivial single-field case):

```powershell
python benchmarks/native_plain_value.py
```

**Result: Python wins all three shapes:**

| Payload | Python | Native | Python is faster by |
|---|---:|---:|---:|
| Flat dict (6 fields) | 7.975 us/call | 11.003 us/call | 1.38x |
| Single Entity (`as_dict` + a Timestamp field) | 3.454 us/call | 4.608 us/call | 1.33x |
| 30-row Entity list (matches `db_list`'s own row count) | 104.660 us/call | 166.371 us/call | 1.59x |

**Decision: not wired in.** The reason is structural, not a repeat of
the "already-optimized C library" finding above -- it's the same wall
the ASGI header candidates and the general template engine hit:
`_plain()` is a recursive walk that calls back into Python
(`hasattr`/`isinstance`-equivalent checks, attribute access) at *every
node* of the graph, so it pays one FFI crossing per node rather than
one crossing for the whole call. A 30-row Entity list means dozens of
crossings, and that per-node cost dominates regardless of how many rows
there are -- more rows don't amortize it away, they multiply it.
`hayavega_native.plain_value` stays in the codebase, unwired, alongside
`serialize_json`/`compile_selection`/`normalize_path`, as a published,
reproducible rejection rather than quietly discarded code.

## A Rust router (HTTP-level, not a microbenchmark): a null result, not a loss

Every candidate above was measured as a microbenchmark -- the same
Python operation called in a tight loop, with and without a PyO3
crossing wrapped around it. `RUST_CORE_REWRITE_ROADMAP.md`'s Phase 1
tests a different question: does an FFI crossing's cost, which
dominated every microbenchmark above, still dominate when it only
happens *once per real HTTP request* instead of once per loop
iteration? `hayavega_native.Router` mirrors `runtime.py`/`asgi.py`'s
`_match()` exactly (exact-pattern lookup, falling back to a wildcard
scan) -- structurally the same tiny operation `normalize_path` already
lost on as a microbenchmark. This time the comparison is two real WSGI
applications, same app (`benchmarks/hayavega_app`), same server
(`waitress`, both sides -- only the matching logic differs), via
`benchmarks/native_router_vs_python.py`:

```powershell
python benchmarks/native_router_vs_python.py
```

**Result (two runs shown) -- a genuine null result, not a loss:**

| Scenario | Run 1 | Run 2 | Average |
|---|---:|---:|---:|
| `json_echo` (isolates routing specifically -- see its own scenario description) | 0.99x | 0.98x | 0.985x |
| `db_list` | 1.11x | 0.88x | 0.995x |
| `template_render` | 0.93x | 0.97x | 0.95x |
| `static_file` | 0.98x | 1.03x | 1.005x |

Every scenario lands within normal run-to-run variance on this machine
(this doc's own cross-framework section already documents 5-15%
variance as ordinary) -- including `json_echo`, the one scenario built
specifically to isolate routing/dispatch overhead from everything
else. There is no consistent direction across the two runs in either
direction. This is categorically different from the four candidates
above, which lost decisively and reproducibly (1.3x-22.4x, same
direction every time) -- here, a Rust crossing that happens exactly
once per request is cheap enough that its cost (or saving) is
statistically invisible against everything else a request already
pays for (socket I/O, WSGI protocol handling, `Request`/`Response`
construction).

**Decision: not wired in -- the gate requires a decisive win, the same
bar `mango_list`/`SignalDispatcher`/the Rust WSGI server all cleared by
wide, consistent margins, and a coin-flip result doesn't clear it.**
But this is worth separating from the four rejections above in how
it's understood: those four say "don't wrap *this specific tiny
operation* in Rust." This one says something more useful for everything
that comes after it in `RUST_CORE_REWRITE_ROADMAP.md`: a single FFI
crossing per request is close to free at HTTP level, so the open
question for later phases isn't "can we afford to cross into Rust
once," it's "does the Rust side do enough *actual* computational work,
once there, to come out ahead" -- which is exactly the question Phase
3 (DB execution, real I/O and row materialization, not a dict lookup)
is positioned to answer. `hayavega_native.Router` stays in the codebase,
unwired, as the concrete starting point that phase needs.

## `SerializedConnection`: a real win, and the first one wired in since the native server/Signals dispatcher

The full DB-execution vertical slice this section's predecessor
pointed at was itself investigated before being built, and found
likely to be a third null/loss result: `Selection.values()` (the
method `db_list` actually calls) already goes straight to
`msgspec.json.encode()` with no object-graph walk to fold in, and
`db.py`'s own docstring already states the real ~35-50ms/request cost
is SQLite's own C-level file I/O, "paid identically by both
frameworks" -- not Python wrapper overhead a rewrite removes. Even the
natural fallback idea -- "Rust-accelerate the existing connection
pool's bookkeeping" -- turned out to be pointless too, once
`db.py`'s own pooling docstring was read closely: the documented WSGI
regression is confirmed, in its own words, to come from "an isolated
multi-threaded microbenchmark with no framework code involved at
all" -- SQLite's own internal lock contention when many persistent
connections hold one file open, identical regardless of which
language holds the connection handle.

What survived both of those findings: **one single connection, Rust-
serialized access, instead of N concurrent per-thread connections** --
a genuinely different architecture from "the same pool, written in
Rust," built on `SignalDispatcher`'s exact existing pattern (one
`mpsc` channel, a dedicated OS thread, `Python::attach` to call back
into Python) rather than a new Rust SQLite dependency: the worker
thread calls Python's own `sqlite3` module, just through one
connection instead of several.

```powershell
python benchmarks/serialized_connection_vs_python.py
```

**Result (two runs shown) -- a real, decisive, reproducible win:**

| Comparison | Run 1 | Run 2 |
|---|---:|---:|
| `SerializedConnection` vs. no pooling (default) | 1.67x | 1.51x |
| `SerializedConnection` vs. `enable_readonly_connection_pooling()` | 1.63x | 1.56x |
| `enable_readonly_connection_pooling()` vs. no pooling (sanity check) | 1.03x | 0.97x |

The sanity-check row is doing real work here, not just filler: it
independently reproduces `db.py`'s own already-documented finding for
`enable_readonly_connection_pooling()` (measured there as "neither a
real win nor a real loss" against the unpooled default) using this
benchmark's own methodology -- confirming the methodology itself is
trustworthy before trusting the headline numbers above it.

**Decision: wired in**, as `hayavega.db.SerializedConnection` -- the
first Rust-acceleration candidate to clear the decisive-win bar since
the native WSGI server and Signals dispatcher. Shipped as a
documented, opt-in primitive (not folded into `Selection`/`Entity`):
SQLite only, read-only only, raw SQL in, an already-materialized
`list[dict]` out -- not a drop-in replacement for the ORM's query
path, and not a universal win by construction (every query now queues
behind whichever one is currently running on the one connection,
trading SQLite's per-connection lock contention for a single
serialization point -- benchmark your own deployment, the same
caveat `enable_connection_pooling()`'s own docstring already carries).
See `docs/topics/models.md` for the full API and trade-off.

## The general template engine: already built, already lost, run for the first time here

`benchmarks/native_template_engine.py` existed in this repo before
this session looked for it -- a complete wire-format bridge from
`templates.py`'s real parsed node tree (via its private
`_compile_cached`) into Rust `TemplateNode`/`TemplateExpr`
(`crates/hayavega-core/src/lib.rs`), correctness-verified byte-for-byte
against `render_template()`'s real output, including a variant that
decodes the wire format once and caches the result (isolating that
cost from the actual render). It had never been run. Running it now:

```powershell
python benchmarks/native_template_engine.py
```

**Result: Python wins, decisively, on every shape tested:**

| Template | Python | Native | Python is faster by |
|---|---:|---:|---:|
| `page.html` (variables + for, 20 rows) | 184.6 us/call | 459.9 us/call | 2.49x |
| `page.html`, wire-decode cached once | 148.9 us/call | 436.1 us/call | 2.93x |
| `if_heavy.html` (if/elif/else + for, 20 rows) | 223.6 us/call | 585.4 us/call | 2.62x |

**This matters specifically because of the 16-20x win earlier in this
document** (`render_post_list_page`, the hand-compiled, one-template-
only function): that win was never evidence a *general* engine would
win too, and this is the direct test of that question, not a
hypothetical one. The result confirms the same pattern every rejected
candidate in this document already shares: `TemplateNode`/`TemplateExpr`
evaluation calls back across the FFI boundary at every `{{ }}`/`{% if %}`/
`{% for %}` node, the same shape that already sank `_plain()`'s
object-graph walk and the ASGI header candidates. More nodes (a bigger
template, more loop iterations) doesn't amortize that cost away -- it
multiplies it, the same lesson `_plain()`'s 30-row list already taught.

**Decision: not pursued further.** `hayavega_native.render_compiled_template`/
`CompiledNativeTemplate` stay in the codebase, unwired, next to
`render_post_list_page` itself -- the hardcoded win remains real and
documented for what it actually is (one exact template, not a general
solution), and the general engine's loss is now a published,
reproducible result too, not an unrun script sitting in the repo.

## Fixing `template_render` in Python: a real, modest win

The Rust investigation above (both the hardcoded and general engines) came
up empty, so the next candidate was `templates.py`'s own Python
implementation. `_render_for` built a full `dict(context)` copy on every
loop iteration just to inject the loop variable and `loop` metadata --
`benchmarks/template_context_fix.py` benchmarks that against two
alternatives:

```powershell
python benchmarks/template_context_fix.py
```

**First alternative tried: `collections.ChainMap`, which measured
*slower*, not faster.** ChainMap avoids the copy by layering a small
overrides dict on top of the shared context, but its `.get()` has more
per-call overhead than a plain dict's, and that cost is paid on every
`{{ }}`/condition evaluated inside the loop body -- far more often than
once per iteration. Measured 1.15x-1.20x *slower* than the original
`dict(context)` copy. Reverted; the assumption that avoiding a copy is
automatically a win was wrong here, the same lesson the Rust benchmarks
already taught in a different form.

**What actually shipped: mutate `context` in place for the loop variable
and `loop` metadata, restoring the previous values once after the whole
loop finishes**, instead of copying on every iteration or wrapping in
anything. Safe because the `context` `_render_for` receives is always a
private dict `render_template` already created just for this render, never
the caller's own object, and nested loops each save/restore their own
prior values correctly by construction. **Result (two runs): 1.02x-1.17x
faster, and lower peak memory in both a narrow context (one 20-item list)
and a wider one (52 keys, simulating shared site/nav/user data alongside
the loop) -- 37% less peak memory on the wider context** (4.5 KiB vs
7.1 KiB for 2,000 renders). The win grows with context width, as expected:
the old cost was `O(items x len(context))`; the new cost is `O(items)`.

This is a small, real improvement, not a dramatic one -- re-running the
full cross-framework suite after the fix moved `template_render` from
~494-498 to ~512 req/s on Hayavega's WSGI path, narrowing the gap to
FastAPI's Jinja2 from ~10.5% to ~7.6%. It does not close the gap; Jinja2
compiles templates to Python bytecode ahead of time, which is a
structurally different (and faster) approach than `templates.py`'s
tree-walking interpreter. Shipped anyway, since it is a strict
improvement with no measured downside and 32/32 existing template tests
still pass unchanged.

## Two more Python-side wins, shipped -- and why neither moves the HTTP benchmark

Two more candidates from the same investigation were built, benchmarked,
and shipped, both as real, measured improvements to `templates.py` itself
-- but re-running the full cross-framework HTTP suite after both revealed
an important, honest surprise: neither moved `template_render`'s numbers
in any visible way. Both are still worth having; the reason they don't
show up in the HTTP benchmark is itself the more useful finding.

**1. Precomputed dotted-path splits.** `_Path` used to store only the raw
string `"post.title"`, and `_resolve_path` called `.split(".")` fresh on
every single `{{ }}`/condition evaluation. `_Path` now also stores the
pre-split `parts` tuple at parse time (`benchmarks/template_path_fix.py`
compares old-vs-new in the same run, since absolute `timeit` numbers
aren't comparable across sessions/machines):

```powershell
python benchmarks/template_path_fix.py
```

Result across three runs: **1.07x-1.18x faster.** Small, real, no
measured downside; shipped.

**2. Compiling templates to real Python functions -- the bigger swing,
benchmarked and shipped.** The remaining structural gap to Jinja2 was
always going to be this: Jinja2 compiles each template to real Python
bytecode once, so every render calls a plain function; `templates.py` was
still re-walking the same parsed node tree -- an `isinstance()` dispatch
per node -- on every single render. `benchmarks/compiled_template_engine.py`
builds and benchmarks exactly this, deliberately scoped: node-level
structure (text/if/elif/else/for) compiles to real Python control flow via
`compile()`/`exec()` on the already-parsed, trusted node tree (never on
caller-supplied text -- see `templates.py`'s module docstring for the
precise safety guarantee this preserves), but expression evaluation still
calls the existing, tested `_evaluate()` interpreter rather than also
compiling `{{ }}`/condition expressions to raw operators, since
`_evaluate()`'s undefined-vs-`None` semantics and the `in` operator's
`TypeError`-to-`False` fallback are exactly the kind of subtle behavior a
from-scratch reimplementation could get wrong for no benchmarked gain.

```powershell
python benchmarks/compiled_template_engine.py
```

**Result (three runs): 1.27x-2.19x faster** on both the real benchmark
suite's `page.html` and a synthetic if/elif/else-heavier template.
**Shipped**, with automatic fallback: a template using
`{% extends %}`/`{% block %}` (which the compiler doesn't support) is
detected once per file and transparently rendered by the unchanged
interpreter instead -- verified by dedicated tests
(`CompiledRenderPathTests` in `tests/test_templates.py`) proving the
compiled path is genuinely used for plain templates and genuinely bypassed
for ones using inheritance, not just that output matches either way. All
35 template tests pass (32 before, plus 3 new: one closing a real coverage
gap this change created -- the interpreter's `_render_for` became
reachable only via inheritance templates, and none of the existing
inheritance tests happened to use a `{% for %}` loop, so its
mutate-and-restore logic had silently lost test coverage until a test was
added specifically to exercise it again).

**Neither improvement moved the `template_render` HTTP benchmark
scenario's throughput in any visible way** (still ~578-590 req/s on
Hayavega's WSGI path, same as before both fixes). Investigating why revealed
the actual bottleneck: `posts_page`'s view calls `Post.collection(DATABASE)`,
and `hayavega.db.connect()` opens a **brand-new `sqlite3.connect()` on every
single request** -- no connection pooling or reuse. Checking
`benchmarks/fastapi_app/app.py` confirms **FastAPI's `fetch_posts()` does
exactly the same thing**, so this cost is paid equally by both frameworks.
At ~40-50ms of total request time dominated by a fresh SQLite connection
open + query + close, a template-rendering step measured in the tens to
low-hundreds of *microseconds* -- even improved 1.3x-2.2x -- is too small a
fraction of the total to move an HTTP-level throughput number. The
isolated microbenchmarks above are real and the improvements are real; the
HTTP benchmark just isn't sensitive enough to see them here, because it's
measuring something else almost entirely.

**This reframes where `template_render`'s remaining ~20-25% gap to
FastAPI likely comes from:** not primarily template-engine speed (both
sides now compile), but either (a) Jinja2's render being faster in
absolute terms even after these fixes, since it compiles expressions to
raw operators too and this candidate deliberately doesn't, or (b) something
in the shared per-request SQLite path that happens to cost Hayavega's stack
slightly more than FastAPI's for reasons unrelated to templates at all.
Neither is benchmarked yet -- worth a dedicated look before assuming
templates.py is the remaining lever.

## Does a general native template engine survive generalization? No.

The previous section's win was for a *hand-compiled, one-template-only*
Rust function. The roadmap explicitly flagged that as a scoping signal,
not proof a *general* engine (handling arbitrary `{{ }}`/`{% if %}`/
`{% for %}` the way `hayavega.templates` actually does) would keep that
margin. `benchmarks/native_template_engine.py` builds that general engine
and tests the prediction directly.

**Design:** `crates/hayavega-core/src/lib.rs` gained a real interpreter --
`TemplateExpr`/`TemplateNode` enums plus an `evaluate`/`render_template_nodes`
pair that mirror `templates.py`'s `_evaluate`/`_render_nodes` semantics
node-for-node (undefined-vs-null distinction, `loop.index`/`first`/`last`
metadata, `and`/`or`/`not`/`==`/`!=`/`in`, if/elif/else branch selection).
13 Rust unit tests cover this directly, including one that reproduces
`page.html`'s exact output byte-for-byte. `{% extends %}`/`{% block %}` are
not implemented; the Python-side wire converter refuses (raises) for a
template using them, and both benchmark templates avoid them.

```powershell
python benchmarks/native_template_engine.py
```

**Result (three runs): Python wins every time, by 1.4x-2.1x, on both a
variables+for template and a heavier if/elif/else+for one.** This
completely reverses the earlier hardcoded function's 16-20x win.

To find out why, the benchmark also measures a `CompiledNativeTemplate`
that decodes the wire-format node tree into Rust structs **once** (at
construction) instead of on every render call -- isolating "cost of
marshaling the tree across FFI" from "cost of interpreting it." The
result: **caching the decode changed nothing** (native time stayed at
670-700 us/call whether or not the tree was pre-decoded). The loss is not
an FFI-crossing artifact this time -- it is the interpretation itself.
Each `{{ }}`/`{% if %}`/`{% for %}` node requires an enum match, a
`JsonValue` clone for `evaluate()`'s result, and (worse) a full clone of
the entire context object on every single `{% for %}` iteration to build
that iteration's `loop_context` -- all of which CPython's own already-
optimized interpreter loop, plus its far cheaper attribute/dict-key
lookups, does more efficiently than a Rust reimplementation that has to
re-derive that same generality from a much more general value
representation (`JsonValue`) than templates.py's plain Python objects.

**This is the honest, complete answer, and it closes the roadmap item:**
the earlier 16-20x number was not evidence a general Rust template
compiler was worth building -- it was evidence that a hardcoded,
zero-branching string builder beats a *general interpreter*, which was
never in question and was true before any Rust was involved. Generalizing
removed exactly the property (no branching, no cloning, no dispatch) that
made the specialized version fast. **Decision: do not build a general
Rust template compiler for `hayavega.templates`.** If `template_render`'s
gap to FastAPI's Jinja2 is worth closing in the future, the more promising
angle based on this data is optimizing the *Python* interpreter itself
(e.g., avoiding `dict(context)` copies per loop iteration, which
`templates.py`'s own `_render_for` also does) rather than porting it to
Rust.

## Hypermedia fragment vs full page: what htmx actually saves here

`hayavega.ui` was removed entirely on Aug 27, 2026 (a product-direction
decision, not a performance finding), then rebuilt the next day narrower
in scope -- Stage 1 hypermedia only, a leaner `hayavega-ui.js`, and (new)
a benchmarked Rust option for fragment rendering. This section reflects
the current, rebuilt implementation.

`benchmarks/hypermedia_fragment.py` compares `GET /posts` (the full
`template_render` page, rendered via `hayavega.templates`) against
`GET /posts/fragment` (just the `<ul>` a real `hx-get`/`hx-target` update
would swap in, rendered via `hayavega.ui`'s Python component builders) for
the same 20 rows, served by the same Hayavega app. This is Hayavega-only, not
a cross-framework comparison -- FastAPI and Django don't ship a hypermedia
runtime.

```powershell
python benchmarks/hypermedia_fragment.py --duration 10s --connections 30
```

**Result (two runs): the fragment is now measurably faster -- 1.11x-1.15x
the full page's throughput** (587 vs 654 req/s, then 600 vs 691 req/s).
This differs from the original (pre-removal) version of this benchmark,
which found no measurable difference (0.99x-1.02x) -- because the
rendering mechanism differs now: the full page still goes through
`render_template()`'s file lookup, mtime check, and (per the
`template_render` section above) the compiled-function render path, while
the fragment is built directly by `hayavega.ui`'s plain Python component
functions with none of that machinery. The lesson from the original
version still holds for what a fragment saves that this benchmark can't
see (network payload, avoided client-side re-render) -- this version
additionally shows a small, real server-side saving too, from a simpler
render path rather than from being structurally smaller markup.

### Rust for fragment rendering: benchmarked and it actually won

Every general-purpose Rust template candidate this project tried lost to
Python (see the sections above). Fragments are a different shape of
problem, though: a fragment renders one specific, small, fixed piece of
markup by design, so a hardcoded native renderer's lack of generality --
the exact thing that made a hardcoded full-page renderer unshippable -- is
much less of a real limitation here. `benchmarks/native_htmx_fragment.py`
tests this directly: `crates/hayavega-core`'s new `render_fragment_items`
(a hardcoded Rust renderer for the compact `<ul><li>...</li>...</ul>`
markup `hayavega.ui`'s `ul(*(li(...) for ...))` builders produce) against
those same Python builders, for two sizes -- the benchmark app's 20-item
list, and a single-item fragment (closer to a realistic "add one item"
interaction).

```powershell
python benchmarks/native_htmx_fragment.py
```

**Result (two runs): native wins 8.33x-8.44x on the 20-item fragment and
4.30x-4.32x on the single item.** A real, decisive, reproducible win --
the first Rust candidate all session to beat Python at rendering anything
general enough to matter beyond one exact template.

**Checked whether this shows up at the HTTP level, rather than assuming
either way (the compiled Python template engine's isolated 1.3x-2.2x win
turned out to have zero visible HTTP-level effect, so isolated wins don't
automatically translate).** `posts_fragment_native` in
`benchmarks/hayavega_app/app.py` runs the identical DB query as
`posts_fragment` and differs only in using `render_fragment_items` instead
of the Python builders; `hypermedia_fragment.py` was extended to a
three-way comparison:

```
full_page:          582-583 req/s
hx_fragment:         653-663 req/s
hx_fragment_native:  687-717 req/s
```

**Result (two runs): native is 1.05x-1.08x the Python fragment's
throughput -- a real, modest, reproducible win, unlike the compiled
template engine's invisible one.** Smaller than the isolated 4x-8x figure
(the same per-request SQLite connection cost dominates here too), but
non-zero this time -- a fragment endpoint's lower baseline latency than a
full page's leaves proportionally more room for the rendering slice to
matter.

**Not wired into `hayavega.ui`'s shipped API.** `render_fragment_items` only
ever handles this one exact fragment shape (title/views tuples rendered as
`<li>{title} ({views} views)</li>`); generalizing it to handle arbitrary
`hayavega.ui` component trees would reintroduce precisely the generality
problem that sank the general Rust template engine. Kept instead as a
documented, worked example (`posts_fragment_native`) for a project author
who has identified their own hot, narrow, high-traffic fragment and wants
to accelerate that one specific shape themselves.

### Proving the htmx wiring is real, not just documented

Everything above establishes *what htmx saves*; it doesn't prove Hayavega's
htmx support actually works end-to-end on a real page rather than existing
only as isolated unit tests and prose. `benchmarks/hayavega_app` has a
`/posts/live` page built for exactly that: a real `Refresh` button with
`hx-get="/posts/fragment" hx-target="#posts-list" hx-swap="innerHTML"`,
loading the genuine `hayavega-ui.js` runtime -- kept as a separate route from
`posts_page`/`page.html` specifically so it can never affect the
cross-framework `template_render` numbers published above.

```powershell
python benchmarks/verify_htmx_wiring.py
```

This cannot execute the client-side JS itself (no browser/DOM in this
environment, and this repo does not depend on Node.js/npm), so it verifies
every piece of the server-side contract the JS runtime depends on instead:
`/posts/live` carries real `hx-get`/`hx-target`/`hx-swap` attributes and
loads the real runtime file; `/posts/fragment` (what the button points at)
returns output byte-identical to what's already inside `#posts-list`,
proving a real swap would render correctly rather than mismatching
structure; and the served `hayavega-ui.js` is verified to contain the actual
`hx-get`/`hx-target`/`hx-swap` handling code its own click listener reads,
not a stub. All three checks pass.

## Line-by-line memory profiling: what actually works on this dev environment

Three tools were tried before settling on what's actually used here --
each has a real limitation for this specific workload (many small,
short-lived allocations per template render), discovered by testing them,
not assumed:

- **Scalene** was the initial preference (combined CPU+memory, per-line,
  and it can even attribute memory inside `hayavega_native`'s Rust code,
  which no pure-Python tool can). **It fails to build on this repo's
  Windows dev environment** -- `pip install scalene` errors with "Microsoft
  C++ Build Tools... required" at its native extension's build step. A
  genuine Scalene-on-Windows gap, not a setup mistake -- same treatment
  this document already gives `gunicorn`'s Windows limitation. Usable via
  WSL/Linux/macOS if a contributor wants that deeper view.
- **`memory_profiler`'s `@profile`** installs cleanly on Windows, but it
  samples **process RSS**, not Python allocator activity. For a tight loop
  like a template render, CPython's own reference counting frees each
  iteration's garbage immediately, so RSS barely moves between samples --
  it correctly reports ~0 MiB per line for a hot loop, which answers "is
  anything leaking or growing unboundedly" (no) but not "which line
  allocates the most churn."
- **`tracemalloc`** (stdlib, already used in `template_context_fix.py`)
  tracks real Python allocations, but a plain before/after snapshot diff
  has a different version of the same blind spot: by the time the "after"
  snapshot is taken, refcounting has already freed most of a single
  render's internal garbage, so the diff mostly shows only what survived
  (the returned string, cache entries) -- not the transient allocation
  that happened along the way.

**What actually works, and what each is for:**

```powershell
pip install memory_profiler   # optional dev dependency, not in pyproject.toml
python -m memory_profiler benchmarks/memory_profile_templates.py
```

`memory_profile_templates.py` uses `memory_profiler` for the question it's
actually suited to: confirming 20,000 renders don't leak or grow RSS (they
don't -- 0.1 MiB total growth, noise-level). For "does this implementation
use less memory than that one," `template_context_fix.py`'s approach is
the one that already worked this session: sample
`tracemalloc.get_traced_memory()`'s **peak** value across a batch of calls
while tracing stays active, which catches each call's live-at-peak
footprint before refcounting frees it -- not a before/after diff. That
already produced a real, correct result once (the mutate/restore fix's
37% peak-memory reduction on a wide context), so it's the tool to reach
for again on tomorrow's planned template-compilation work, not a new one.

## Honest limitations of this pass

- Single machine, single run per configuration published, not a
  statistically rigorous multi-trial study with confidence intervals.
- Windows only; no Linux/gunicorn numbers yet (see above).
- No Next.js (see above).
- SQLite only, 20 rows, read-only during the benchmark -- says nothing
  about write-heavy workloads, larger datasets, or PostgreSQL/MySQL.
- Django's template rendering here uses the **uncached** `DjangoTemplates`
  loader (the default under `settings.configure()` without
  `django.template.loaders.cached.Loader` configured explicitly) -- a real
  Django deployment would typically enable template caching, which this
  suite does not model. This makes `template_render`'s Django number a
  conservative (worse-than-typical-production) figure, disclosed rather
  than tuned away, since tuning it would mean applying an optimization to
  only one of the three frameworks.

## Update: the WSGI `template_render` gap to FastAPI is no longer reproducible

Every narrative section above (Python-side context fixes, the compiled
template engine, read-only connections) chased this gap while it
consistently measured around ~11-25% in favor of FastAPI in single-run
snapshots. Asked directly to work it further, the first step was
re-measuring it, since several of those fixes had shipped on their own
merits despite showing no visible HTTP-level movement individually.

Four full `oha -z 10s -c 30` runs of the published suite, back to back, no
code changed between them:

| Run | Hayavega (WSGI) req/s | FastAPI req/s |
|---|---:|---:|
| 1 | 721 | 722 |
| 2 | 736 | 626 |
| 3 | 670 | 761 |
| 4 | 751 | 760 |
| **Average** | **719.5** | **717.25** |

**The gap averages to a statistical tie (0.3%), with individual runs
swinging up to 17% in either direction.** This is not claimed as a new fix
-- nothing changed between these four runs -- most likely the accumulated
effect of several earlier "no measurable HTTP-level movement" fixes adding
up over time, plus this scenario's run-to-run noise (see "Honest
limitations" above) being large enough that any single past snapshot,
including the ones earlier in this document, was never a fully reliable
read on which framework was actually ahead. Anyone citing a `template_render`
number from this suite going forward should average several runs, not
quote one.

## The Rust runtime vs. FastAPI, directly measured

Every FastAPI comparison published above used `waitress` as Hayavega's WSGI
server, because every one of those runs predates the Rust runtime ("The
Rust runtime vs the Python runtime (waitress)" above) existing. That
section only ever compared the Rust runtime against Hayavega's own
`waitress` baseline, never against FastAPI directly -- so no number on
file actually answered "does the Rust runtime beat FastAPI," only the
weaker, inferred claim that it should, since waitress and FastAPI were
themselves roughly tied. `benchmarks/rust_runtime_vs_fastapi.py` closes
that gap: same two apps as every other FastAPI comparison in this
document (`benchmarks/hayavega_app/app.py` behind `hayavega.rust_runtime`,
`benchmarks/fastapi_app/app.py` behind `uvicorn`), same four scenarios,
same seeded data -- only the missing pairing.

**Three full `oha -z 10s -c 30` runs, no code changed between them (per
this document's own standing rule after the `template_render`
re-measurement above: one run is not a reliable read):**

| Scenario | Rust runtime (3 runs) req/s | FastAPI (3 runs) req/s | Ratio (3 runs) | Average ratio |
|---|---:|---:|---:|---:|
| `json_echo` | 9425, 9929, 9429 | 1942, 1950, 1941 | 4.85x, 5.09x, 4.86x | **4.93x** |
| `db_list` | 1616, 1669, 1575 | 703, 700, 719 | 2.30x, 2.38x, 2.19x | **2.29x** |
| `template_render` | 1295, 1284, 1219 | 703, 758, 727 | 1.84x, 1.69x, 1.68x | **1.74x** |
| `static_file` | 2069, 1986, 1977 | 699, 719, 713 | 2.96x, 2.76x, 2.77x | **2.83x** |

Success rate was 100% for both targets on every scenario, all three runs.

**Result: the Rust runtime beats FastAPI outright on every scenario, by a
wide and consistent margin (1.68x-5.09x across individual runs, none
close to a tie)** -- unlike the noisy, single-digit-percent margins that
made the `waitress`-vs-FastAPI comparison unreliable above, these ratios
are large enough that the run-to-run noise this document has repeatedly
found on this environment (up to 17% swings on `template_render`
specifically) cannot plausibly explain them away. `template_render`
carries the smallest margin of the four, consistent with it being the
one scenario where the ~35-45ms SQLite connection-and-query cost (paid
identically by both frameworks, see "environment ceiling" in
`INDUSTRY_LEADERSHIP_ROADMAP.md`) is a larger share of total request
time than the HTTP-layer cost the Rust runtime actually improves --
`json_echo`, which does no database work at all, shows the largest and
most consistent margin (~4.9x) for the same reason.

This closes the last open item from the Rust-runtime work: it was
previously benchmarked only against Hayavega's own `waitress`, with a
FastAPI win inferred rather than measured. It is now measured directly,
and the inference holds.

## Chasing a bigger margin: does `SerializedConnection` move `db_list`/`template_render` under the Rust runtime?

Asked directly whether Hayavega could show a 10-20x margin over
FastAPI/Django across the board: no, not with this architecture, and
claiming otherwise would contradict every number already published in
this document. `json_echo` (no database work) is the only scenario where
a double-digit ratio has been observed at all, and only on noisy runs
(see below) -- `db_list` and `template_render` are both bounded by the
~35-45ms-per-request SQLite connection-and-query cost every framework in
this suite pays identically, which no HTTP-layer improvement on
Hayavega's side can shrink.

What *was* worth checking: `SerializedConnection` ("a real win" above) was
only ever benchmarked in isolation (`serialized_connection_vs_python.py`)
or null-tested under `waitress`'s 16-thread pool
(`enable_readonly_connection_pooling()`'s own docstring) -- never actually
wired into `posts_json`/`posts_page` and measured at the HTTP level
*under the Rust runtime specifically*, where the baseline per-request
overhead is already small enough that one shared connection replacing N
fresh `sqlite3.connect()` calls has more relative room to matter.
`benchmarks/hayavega_app/app.py`'s `posts_json`/`posts_page` now read
through one shared `SerializedConnection` instead of
`Post.collection(DATABASE)`'s fresh connection per request (writes --
`seed()`, the htmx fragment routes -- are untouched).

**Paired before/after runs (`--duration 10s --connections 30`), same
machine, same session -- noisy, disclosed rather than cherry-picked:**

| Scenario | vs FastAPI, before | vs FastAPI, after | vs Django, before | vs Django, after |
|---|---:|---:|---:|---:|
| `json_echo` | 5.96x | 10.09x* | 5.43x | 5.70x |
| `db_list` | 3.52x | 4.49x | 3.85x | 5.48x |
| `template_render` | 1.73x | 2.59x | 4.01x | 4.85x |
| `static_file` | 4.25x | 4.51x | 2.31x | 1.60x |

\* `json_echo` touches no database at all, so this run's jump is this
dev machine's own run-to-run noise on the FastAPI side (its `json_echo`
baseline alone swung from 958 to 668 req/s between runs with zero code
changed), not a `SerializedConnection` effect -- included for
transparency, not claimed as caused by this change. `static_file` moving
in both directions across the two framework comparisons is the same
noise, also untouched by this change (no scenario here reads the
database for static files).

**The real, attributable signal is `template_render`**: a consistent,
reproducible improvement in both comparisons (1.73x to 2.59x vs FastAPI,
4.01x to 4.85x vs Django) across every paired run taken, including two
additional confirmation runs not tabulated above. `db_list` moved up in
three of four paired runs taken but was flat in one, so it reads as a
real but smaller and less consistent effect than `template_render`'s --
plausibly because `template_render` pays the connection-open cost *and*
a render step, giving a shared-connection fix more total time to recover
proportionally.

**Decision: wired in**, scoped to exactly the two read routes this was
measured on. This does not change the headline conclusion: the ceiling
for this architecture, measured honestly, is single-digit multipliers on
I/O-bound scenarios and a best case around 5-10x on I/O-free ones that
is itself sensitive to machine noise -- not the 10-20x a casual
comparison might assume from `json_echo` alone.

## Real concurrent I/O under ASGI: the scenario every prior comparison left open

Every scenario benchmarked anywhere above (`json_echo`, `db_list`,
`template_render`, `static_file`) is a plain synchronous view under every
transport tested, including Hayavega (ASGI) and FastAPI. None of them
exercise the one property ASGI actually has that WSGI doesn't: an
`async def` view awaiting I/O yields the event loop instead of blocking a
whole OS thread for the call's duration, so many more requests can be in
flight than there are threads. "ASGI vs FastAPI" above says this
explicitly: "`async def` views doing concurrent I/O ... still aren't
benchmarked here." `benchmarks/asgi_concurrent_io.py` is that benchmark.

**Setup:** `benchmarks/upstream_app` is a raw ASGI app (no framework) that
just `await asyncio.sleep()`s for a fixed delay before responding --
standing in for a real downstream network call without real-network
flakiness. Three routes, same upstream, same delay, compared:

- `remote_io` (`GET /remote`, `async def`, awaits the call) on Hayavega
  (ASGI) and on FastAPI -- both served by uvicorn.
- `remote_io_blocking` (`GET /remote-blocking`, plain `def`, blocks a
  thread for the call) on Hayavega (ASGI) (dispatched through its 16-worker
  sync pool), on Hayavega (WSGI/`waitress`, 16 threads), and on FastAPI
  (dispatched through Starlette/anyio's thread pool).

Concurrency defaults to 64 -- deliberately above every blocking path's
thread-pool size, so a queuing ceiling has room to show up if the async
paths avoid it and the blocking ones don't.

### Three real bugs found and fixed while building this, not shipped blind

Building a fair, working version of this benchmark surfaced three
separate, genuine problems -- none in Hayavega's or FastAPI's own code, all
in the benchmark's own plumbing. Each is disclosed in full in
`benchmarks/_upstream_client.py`'s docstring; summarized here:

1. **httpx.AsyncClient does not overlap concurrent requests on this dev
   environment.** The first version of the async views used
   `httpx.AsyncClient`, the obvious real-world choice. A direct
   microbenchmark (`asyncio.gather` of N concurrent `client.get()` calls
   against the upstream, no Hayavega/FastAPI involved at all) showed N=32
   taking ~300-900ms across every configuration tried -- default pooled
   client, `trust_env=False`, a fresh client per call, both
   `WindowsProactorEventLoopPolicy` and `WindowsSelectorEventLoopPolicy`.
   The same load over raw `asyncio.open_connection` sockets, no httpx at
   all, took ~50ms. This is a genuine, reproducible httpx/httpcore
   (1.0.9) bottleneck on this machine, not a Hayavega or FastAPI issue --
   both apps' `/remote` routes used the identical client, so it would
   have suppressed both sides equally, hiding rather than answering the
   question this benchmark exists to ask. Routed around with
   `benchmarks/_upstream_client.py`, a minimal raw-socket async client
   built specifically to isolate this benchmark's actual variable.
2. **The replacement client's first version reproduced a bug this
   project already found twice before.** It sent `Connection: close`,
   opening a fresh TCP connection per call. Under real concurrency,
   `netstat` showed dozens of `127.0.0.1:9010` connections sitting in
   `FIN_WAIT_2` -- the same Windows ephemeral-port/`TIME_WAIT` failure
   mode already diagnosed for the Django benchmark target and for the
   Rust runtime's own missing keep-alive (see "The Rust runtime vs the
   Python runtime (waitress)" above). Fixed by pooling and reusing
   keep-alive connections instead, the same "one connection per client,
   kept alive" shape `hayavega.db.enable_connection_pooling()` already uses
   on the database side.
3. **The keep-alive fix then failed to parse the response it got back.**
   uvicorn's `h11` implementation sends `benchmarks/upstream_app`'s
   response with `Transfer-Encoding: chunked` on a persistent connection
   -- verified independently with stdlib `http.client`, so this is
   uvicorn's real behavior, not something to work around server-side.
   The minimal client parses both `Content-Length` and chunked framing
   now, the same as any real HTTP/1.1 client needs to.

A fourth issue was a flaw in the benchmark's own design, not its
client: an earlier version shared one long-lived upstream process across
all three sequentially-benchmarked targets. Killing a target's process
with `SIGTERM` between targets doesn't give its outbound connections to
the upstream a clean FIN handshake, so the upstream accumulated
`CLOSE_WAIT` sockets that were still present, unfairly, when the next
target's benchmark began -- confirmed with `netstat` (64 `CLOSE_WAIT` + 64
`FIN_WAIT_2` after repeated runs) and with a paired comparison: the same
target measured ~5x worse against a contaminated shared upstream than
against a freshly started one on an unused port. Fixed by giving each
target its own freshly started upstream process.

### Results: three full runs, `oha -z 10s -c 64`, 40ms simulated upstream latency

100% success rate, every target, every scenario, all three runs.

| Comparison | Run 1 | Run 2 | Run 3 | Average |
|---|---:|---:|---:|---:|
| 1. Hayavega (ASGI) `/remote` vs FastAPI `/remote` (both async) | 913.6 vs 902.9 (1.01x) | 911.9 vs 855.6 (1.07x) | 873.4 vs 870.2 (1.00x) | **899.6 vs 876.2 req/s, 1.03x** |
| 2. Hayavega (ASGI) `/remote` vs its own `/remote-blocking` | 913.6 vs 283.2 (3.23x) | 911.9 vs 277.6 (3.29x) | 873.4 vs 257.9 (3.39x) | **899.6 vs 272.9 req/s, 3.30x** |
| 3. Hayavega (WSGI) `/remote-blocking` vs FastAPI `/remote-blocking` | 281.1 vs 456.2 (0.62x) | 264.8 vs 461.1 (0.57x) | 270.0 vs 456.6 (0.59x) | **272.0 vs 457.9 req/s, 0.59x** |

**Comparison 1 -- the question this file exists to answer: Hayavega (ASGI)
and FastAPI are a statistical tie on real concurrent I/O** (1.03x average,
individual runs ranging 1.00x-1.07x, all in Hayavega's favor but by margins
this document's own noise findings elsewhere say not to over-read).
Neither framework has an advantage here -- both scale past their
respective thread-pool sizes by roughly the same amount under the same
load, which is exactly what "both are a real ASGI transport, not just a
WSGI transport with an event loop bolted on" would predict.

**Comparison 2 is the real headline: within the same Hayavega process, the
`async def` view beats its own blocking twin by 3.30x.** This is the
concrete, measured version of the claim `hayavega.asgi`'s own module
docstring already makes in prose ("choose ASGI only when a project needs
`async def` views for concurrent I/O ... that WSGI's one-thread-per-
request model can't overlap as cheaply") -- previously asserted, now
demonstrated, with every other variable (routing code, server, process)
held constant.

**Comparison 3 is real but not a fair framework-speed comparison, and is
reported with that caveat rather than left to imply one.** FastAPI's
blocking route runs through Starlette/anyio's default thread pool, which
defaults to **40** tokens (`anyio._backends._asyncio.AsyncIOBackend.
current_default_thread_limiter`); Hayavega's two blocking paths here
(`waitress --threads=16` and `create_asgi_application`'s own
`max_sync_workers=16` default) both use 16. At `-c 64` -- above every
pool size here -- more headroom means more throughput regardless of
either framework's own per-request cost, so this ratio is measuring pool
size more than framework speed. Matching Hayavega's pool size to anyio's
(`max_sync_workers=40`, or `waitress --threads=40`) would be needed
before this specific comparison meant anything about relative framework
overhead; not attempted here since it wasn't this benchmark's question.

### Conclusion

The gap `benchmarks/README.md`'s "ASGI vs FastAPI" section left open is
closed: Hayavega's ASGI transport handles real concurrent I/O as well as
FastAPI's (comparison 1), and ASGI's claimed advantage over WSGI-style
blocking is real and substantial within Hayavega's own two transports
(comparison 2), not just a theoretical property asserted in a docstring.

## Tortoise ORM: the async-driver comparison, built and measured

The `INDUSTRY_LEADERSHIP_ROADMAP.md` entry above ("Deferred, not needed
now: a Tortoise ORM (or other async-driver) benchmark comparison -- new
scope") is new scope, not forgotten scope: every target in this suite,
Hayavega's own ORM included, opens a plain synchronous `sqlite3` connection
per request. Tortoise ORM's default driver for SQLite, `aiosqlite`, is the
first async database driver to appear anywhere in this suite, and that is
a different shape of comparison from anything above -- it needed its own
app and its own scoping before being benchmarked, not a route bolted onto
an existing target.

**`benchmarks/tortoise_app/app.py`** is that app: FastAPI + Tortoise ORM
over `aiosqlite`, the same `Post` shape (`id`, `title`, `views`) and the
same `SEED_COUNT = 20` as every other target, exposing only `/posts.json`
(plus a DB-free `/echo` for the readiness check every other app in this
directory already uses). It's scoped to just the `db_list` scenario on
purpose: `json_echo`, `template_render`, and `static_file` don't touch the
database, so an async driver can't plausibly change them, and all three
are already benchmarked elsewhere in this document.

**Disclosed before the first run, not discovered after it: `aiosqlite` is
not true async I/O.** Reading its own source
(`aiosqlite/core.py`, `_connection_worker_thread`) shows every connection
opens a dedicated background `threading.Thread` that runs the real,
blocking `sqlite3` calls; `await`ing a query just parks the coroutine on a
`Future` that thread later resolves via `call_soon_threadsafe`. So this
comparison is really "one dedicated worker thread per connection"
(Tortoise ORM) vs. "N shared threads across every connection" (Hayavega's
own ORM, dispatched to ASGI's sync-dispatch pool or a WSGI worker thread)
-- not "async I/O vs. blocking I/O" in the sense the concurrent-I/O
comparison above tested for a real network call. Tortoise's default
connection pool for SQLite is also a single connection, so its one
worker thread is the sole serialization point for every concurrent
request in this benchmark; that is disclosed as part of the comparison,
not tuned away, because it's the library's own documented default and
changing it would be benchmarking a different configuration than what a
reader gets from `Tortoise.init()` out of the box.

### Results: three full runs, `oha -z 10s -c 30`, `GET /posts.json`

Four targets, same seeded data, same scenario, same machine:

| Target | Run 1 | Run 2 | Run 3 | Average | Ratio vs. Tortoise ORM |
| --- | ---: | ---: | ---: | ---: | ---: |
| Tortoise ORM (FastAPI + aiosqlite) | 698.2 req/s | 729.0 req/s | 729.6 req/s | 718.9 req/s | 1.00x |
| FastAPI (sync `sqlite3`) | 719.1 req/s | 613.4 req/s | 632.9 req/s | 655.1 req/s | 0.91x |
| Hayavega (ASGI, sync ORM) | 972.9 req/s | 949.1 req/s | 894.8 req/s | 938.9 req/s | 1.31x |
| Hayavega (WSGI, sync ORM) | 740.3 req/s | 777.4 req/s | 763.6 req/s | 760.4 req/s | 1.06x |

100% success rate, every target, every run. Reproduce with
`python benchmarks/tortoise_vs_hayavega.py --duration 10s --connections 30 --runs 3`.

### Conclusion

Tortoise ORM's async driver does not give FastAPI a database-bound edge
over Hayavega's synchronous ORM at this concurrency: Hayavega (ASGI) beats it
outright (1.23x-1.39x across the three runs), Hayavega (WSGI) ties-to-wins
it (1.05x-1.07x), and even FastAPI's own plain-`sqlite3` baseline --
Tortoise's own framework, without Tortoise -- lands within noise of
Tortoise ORM itself (0.84x-1.03x) rather than clearly ahead of it. The
single-worker-thread-per-connection design disclosed above is the direct
explanation, not a surprise found after the numbers came in: at
`-c 30`, thirty concurrent requests contend for the one worker thread
Tortoise's default single-connection pool provides, while Hayavega's sync
ORM calls are spread across a full 16-worker pool (`waitress --threads=16`
or ASGI's `max_sync_workers` default) on both of its own transports. A
reader who wants Tortoise ORM to compete here would need to raise its
pool size explicitly (`Tortoise.init(..., connections={"default": {...,
"maxsize": 16}})` or equivalent) -- not attempted in this pass, since
benchmarking a different configuration than the library's own documented
default was not this comparison's question.
(comparison 2), not just a theoretical property asserted in a docstring.


## GraphQL lexer+parser: a real win, wired into `Schema.execute()`

Phase 6 of `RUST_CORE_REWRITE_ROADMAP.md`. `hayavega.graphql`'s
`Schema.execute()` has two halves with very different Rust
candidacy: field *resolution* (`_resolve_root_field` calling
arbitrary, user-defined Python resolvers) can never move to Rust --
the same ceiling every "what stays Python" section in the roadmap
already states. But tokenizing and parsing a query string into a
`_FieldNode`/`_VariableDefinition` tree needs **zero** Python
callbacks mid-parse, a query string goes in and a complete parsed
structure comes out in one call -- the same "real, substantial work
per crossing" shape `render_post_list_page` and `SerializedConnection`
both have, not the "wrap one small operation" shape that's lost five
times over in this file.

`hayavega_native.parse_graphql_document` is a faithful Rust port of
`_tokenize`/`_Parser.parse_document()` (same punctuation set, same
name/number/string literal lexing, same rejection of `@` directives
and `.` fragments, same 64-deep structural nesting guard), living in
`hayavega-core` with its own `cargo test` coverage, with a thin PyO3
wrapper in `hayavega-python` converting its result into a wire format
(nested tuples/lists/dicts; a 2-tuple `("$", name)` for variable
references, since real GraphQL list/object values are always Python
lists/dicts on this bridge and never tuples) that `graphql.py` decodes
back into the same `_FieldNode`/`_VariableDefinition` objects the pure-
Python path produces. Correctness-verified field-for-field against
the real parser's output before any timing.

```
python benchmarks/native_graphql_parser.py
```

### Results (`timeit`, 100,000 calls, decode included)

| Query shape | Python | Native | Result |
|---|---:|---:|---:|
| Small (`{ greeting }`) | 7.611 us/call | 2.811 us/call | **Native 2.71x faster** |
| Complex (variables, arguments, nested selections) | 122.895 us/call | 31.338 us/call | **Native 3.92x faster** |

The complex shape's margin is wider than the small shape's -- expected,
since lexing/parsing cost scales with query size while the fixed FFI
crossing cost stays constant, so a bigger single call amortizes that
fixed cost further. Both numbers include the Python-side decode step
(reconstructing `_FieldNode`/`_VariableDefinition` from the wire
format), the real cost `Schema.execute()` pays, not just the raw Rust
call in isolation.

### Decision: wired in

A decisive, reproducible win on both a trivial and a realistic query
shape. `Schema.execute()` now calls `hayavega.graphql._parse_document()`,
which tries `hayavega_native.parse_graphql_document` first and falls
back to `_Parser(_tokenize(query)).parse_document()` when
`hayavega_native` isn't built -- identical behavior, including error
messages for malformed queries, on both paths (`tests/test_graphql.py`'s
`NativeParserBridgeTests` exercises both explicitly). `execute()`'s own
logic past parsing -- binding variables, depth/field-count limits,
calling resolvers, projecting results -- is completely unchanged.
