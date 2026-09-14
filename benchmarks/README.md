# Mangoo benchmark suite

Reproducible benchmarks comparing Mangoo against FastAPI and Django on four
scenarios: a minimal JSON response, a DB-backed list read, a server-rendered
HTML page, and static file serving. This is Phase 1 of
[`INDUSTRY_LEADERSHIP_ROADMAP.md`](../INDUSTRY_LEADERSHIP_ROADMAP.md)'s
Performance pillar: nothing about "faster than X" is credible without a
suite like this to back it, so it exists before any optimization work does.

A fourth target, **Mangoo (ASGI)**, was added when Phase 3 added ASGI
support (`mangoo.asgi`): the same routes and seeded data as the original
Mangoo target, served over `uvicorn` instead of `waitress`, so Mangoo's
ASGI transport can be measured against FastAPI on the same server rather
than only against Mangoo's own WSGI path. See "ASGI vs FastAPI" below.

**Next.js is not included in this pass.** It requires npm-installing and
running a full separate Node.js project just to serve as a comparison
target -- a materially bigger lift than FastAPI/Django, which are both
pip-installable and share Mangoo's execution model (a synchronous Python
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
the Mangoo (ASGI) target added when Phase 3 introduced `mangoo.asgi`; no
extra dependency is needed for it.

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

**GraphQL is not a cross-framework scenario.** Mangoo ships GraphQL
support; FastAPI and Django do not, out of the box. Benchmarking it would
mean adding a third-party GraphQL library to each comparison app and
measuring that library's overhead as much as the framework's -- not an
honest apples-to-apples comparison. If you want a Mangoo-only reference
number for `POST /graphql`, `benchmarks/mangoo_app/app.py` already exposes
it; it's just not in the published cross-framework table.

## Servers: why waitress instead of gunicorn

This suite was built and run on Windows, where **`gunicorn` cannot run at
all** -- it imports `fcntl`, a POSIX-only module, and fails at import time,
not just at runtime. There is no way around this on Windows short of WSL or
a container, neither of which this task set up.

The substitute: [`waitress`](https://docs.pylonsproject.org/projects/waitress/),
a pure-Python, cross-platform, production-grade WSGI server, used
identically for **both** Mangoo and Django so the comparison between them
stays apples-to-apples (same server, same thread count, same everything
except the framework). FastAPI is benchmarked behind `uvicorn`, its own
standard/idiomatic server, run single-process (no `--workers`) to match the
single-process nature of the waitress runs.

If you run this suite on Linux/macOS with real `gunicorn` available, that
would be a **more representative** production comparison for Mangoo and
Django specifically -- consider it the natural next iteration of this
suite, not a correction of something wrong with this one.

## A real gotcha this suite ran into (and why the Django app looks the way it does)

Early runs showed Django "failing" 60-95% of requests under load on the
`db_list`/`template_render`/`static_file` scenarios, while `json_echo`
(and Mangoo/FastAPI on every scenario) succeeded 100% of the time. That
looked like a Django performance problem. It wasn't.

Django's `JsonResponse`/`HttpResponse` do not set a `Content-Length`
header themselves -- by WSGI spec, that's the server's job, and Django's
own dev server (which wraps `wsgiref`, the reference WSGI implementation)
computes it for you transparently. `waitress` does not do this inference:
without an explicit `Content-Length` (or `Transfer-Encoding: chunked`
declared some other way), it serves the response as chunked and adds
`Connection: close`, silently disabling HTTP keep-alive for that response.
Mangoo's `Response` sets `Content-Length` itself, so it never hit this.
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

Both FastAPI and Mangoo's ASGI transport now run behind the identical
server (`uvicorn`, single process, no `--workers`), so this is the more
apples-to-apples of the two Mangoo-vs-FastAPI comparisons in this suite --
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
`create_asgi_application(..., max_sync_workers=16)` in `src/mangoo/asgi.py`,
measured against a real `uvicorn` server under real `oha` load, twice
before and twice after.

Four runs at `--duration 10s --connections 30`, two before the fix and two
after:

| Scenario | Mangoo (ASGI), before fix | Mangoo (ASGI), after fix | FastAPI req/s | After-fix result |
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

## About `oha`

[`oha`](https://github.com/hatoo/oha) is a Rust-based HTTP load generator,
chosen because Rust is already part of this repository's toolchain
(`mangoo_native`) -- no new ecosystem introduced for a benchmarking tool,
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

`crates/mangoo-python/src/lib.rs` defines `serialize_json`, `compile_selection`,
and `normalize_path` as native PyO3 functions, but nothing in `src/mangoo/*.py`
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
each surfaced one specific weak spot: Mangoo loses `template_render` to
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
(`benchmarks/mangoo_app/templates/page.html`): a fixed prefix, one loop
emitting `<li>` elements, a fixed suffix. It beats Python's general
tree-walking template *interpreter* by 16-20x because there is real,
amortizable work per call (escaping and formatting 20 rows) instead of a
few string operations, so the FFI crossing cost -- the thing that sank the
first three candidates and the two ASGI ones above -- gets paid back many
times over.

That is a strong enough signal to justify *scoping* a real native Rust
template compiler as a follow-up project. It is **not** evidence that such
a compiler is already built, or that this specific function should be
wired into `mangoo.templates` -- `render_post_list_page` only handles the
one template it was hand-written for; it has no `{% if %}`, no
`{% extends %}`/`{% block %}`, no arbitrary variable paths, none of
`mangoo.templates`'s actual template language. Wiring in a function that
silently only works for one exact template shape while claiming to
replace a general renderer would be a correctness regression dressed up
as a performance win. **Decision: no code wired in from this benchmark
either** -- what changes is the roadmap, where scoping a general Rust
template compiler is now backed by a real measured ceiling instead of a
guess. See `INDUSTRY_LEADERSHIP_ROADMAP.md`'s Phase 3 for the follow-up
item this created.

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
~494-498 to ~512 req/s on Mangoo's WSGI path, narrowing the gap to
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
Mangoo's WSGI path, same as before both fixes). Investigating why revealed
the actual bottleneck: `posts_page`'s view calls `Post.collection(DATABASE)`,
and `mangoo.db.connect()` opens a **brand-new `sqlite3.connect()` on every
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
in the shared per-request SQLite path that happens to cost Mangoo's stack
slightly more than FastAPI's for reasons unrelated to templates at all.
Neither is benchmarked yet -- worth a dedicated look before assuming
templates.py is the remaining lever.

## Does a general native template engine survive generalization? No.

The previous section's win was for a *hand-compiled, one-template-only*
Rust function. The roadmap explicitly flagged that as a scoping signal,
not proof a *general* engine (handling arbitrary `{{ }}`/`{% if %}`/
`{% for %}` the way `mangoo.templates` actually does) would keep that
margin. `benchmarks/native_template_engine.py` builds that general engine
and tests the prediction directly.

**Design:** `crates/mangoo-core/src/lib.rs` gained a real interpreter --
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
Rust template compiler for `mangoo.templates`.** If `template_render`'s
gap to FastAPI's Jinja2 is worth closing in the future, the more promising
angle based on this data is optimizing the *Python* interpreter itself
(e.g., avoiding `dict(context)` copies per loop iteration, which
`templates.py`'s own `_render_for` also does) rather than porting it to
Rust.

## Hypermedia fragment vs full page: what htmx actually saves here

`mangoo.ui` was removed entirely on Aug 27, 2026 (a product-direction
decision, not a performance finding), then rebuilt the next day narrower
in scope -- Stage 1 hypermedia only, a leaner `mangoo-ui.js`, and (new)
a benchmarked Rust option for fragment rendering. This section reflects
the current, rebuilt implementation.

`benchmarks/hypermedia_fragment.py` compares `GET /posts` (the full
`template_render` page, rendered via `mangoo.templates`) against
`GET /posts/fragment` (just the `<ul>` a real `hx-get`/`hx-target` update
would swap in, rendered via `mangoo.ui`'s Python component builders) for
the same 20 rows, served by the same Mangoo app. This is Mangoo-only, not
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
the fragment is built directly by `mangoo.ui`'s plain Python component
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
tests this directly: `crates/mangoo-core`'s new `render_fragment_items`
(a hardcoded Rust renderer for the compact `<ul><li>...</li>...</ul>`
markup `mangoo.ui`'s `ul(*(li(...) for ...))` builders produce) against
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
`benchmarks/mangoo_app/app.py` runs the identical DB query as
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

**Not wired into `mangoo.ui`'s shipped API.** `render_fragment_items` only
ever handles this one exact fragment shape (title/views tuples rendered as
`<li>{title} ({views} views)</li>`); generalizing it to handle arbitrary
`mangoo.ui` component trees would reintroduce precisely the generality
problem that sank the general Rust template engine. Kept instead as a
documented, worked example (`posts_fragment_native`) for a project author
who has identified their own hot, narrow, high-traffic fragment and wants
to accelerate that one specific shape themselves.

### Proving the htmx wiring is real, not just documented

Everything above establishes *what htmx saves*; it doesn't prove Mangoo's
htmx support actually works end-to-end on a real page rather than existing
only as isolated unit tests and prose. `benchmarks/mangoo_app` has a
`/posts/live` page built for exactly that: a real `Refresh` button with
`hx-get="/posts/fragment" hx-target="#posts-list" hx-swap="innerHTML"`,
loading the genuine `mangoo-ui.js` runtime -- kept as a separate route from
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
structure; and the served `mangoo-ui.js` is verified to contain the actual
`hx-get`/`hx-target`/`hx-swap` handling code its own click listener reads,
not a stub. All three checks pass.

## Line-by-line memory profiling: what actually works on this dev environment

Three tools were tried before settling on what's actually used here --
each has a real limitation for this specific workload (many small,
short-lived allocations per template render), discovered by testing them,
not assumed:

- **Scalene** was the initial preference (combined CPU+memory, per-line,
  and it can even attribute memory inside `mangoo_native`'s Rust code,
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
