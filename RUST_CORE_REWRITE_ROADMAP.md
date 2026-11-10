# Rust core rewrite — a phased roadmap

**Status: scoped, not started.** This document is the deliverable from
an explicit decision to pursue a full Rust rewrite of Hayavega's core,
made after this session's own benchmarks found that migrating
individual functions in isolation loses to Python/C every time it was
tried (see `benchmarks/README.md`'s three native-function benchmark
sections). This is not a same-session implementation — it's the
serious, phased plan that decision calls for, sized against the
actual current codebase (~8,864 lines of pure-Python core across 46
modules, validated by ~7,913 lines of existing tests) rather than
against a guess.

## The honest tension this roadmap has to resolve

Every Rust migration attempted in this project so far failed the same
way, for the same structural reason:

| Candidate | Result | Why |
|---|---:|---|
| `serialize_json` vs `msgspec.json.encode` | Python 21.5-22.4x faster | Already racing a specialized C library |
| `compile_selection` vs Python string-building | Python 1.4-2.9x faster | FFI crossing costs more than the few µs of work saved |
| `normalize_path` vs Python transliteration | Python 1.4-1.5x faster | Same — trivial work, fixed crossing cost dominates |
| ASGI header decode/encode | Python 1.4-2.7x faster | Same |
| `_plain()` object-graph walk | Python 1.3-1.6x faster | A recursive walk crossing into Python at every node |
| `render_post_list_page` (hardcoded template) | **Native 16-20x faster** | The one exception: real, amortizable work (escaping/formatting 20 rows) *per crossing* |
| General template engine (Phase 5) | Python 2.49-2.93x faster | Per-node crossing, same wall as `_plain()` -- see Phase 5 |
| `SerializedConnection` (Phase 3) | **Native 1.5-1.7x faster** | A different architecture, not a wrapped function -- one Rust-owned connection serializing access |
| GraphQL lexer+parser (Phase 6) | **Native 2.7-3.9x faster** | Whole-query parse per crossing -- real, substantial, self-contained work, same shape as the template win |

The pattern is consistent: **a Rust function wins only when it does
substantial work per PyO3 boundary crossing.** Every rejected
candidate wrapped one small, narrow operation while everything around
it stayed Python — paying a fixed FFI cost without enough work to
amortize it. The one win did real, batched work in a single call.

**This roadmap's central design decision follows directly from that
data**: migrate **vertical slices of the request path** (routing →
DB execution → row materialization → serialization, as one Rust call)
rather than **individual horizontal functions** (take `_plain()`,
wrap it, ship it) the way every rejected candidate did. A full rewrite
done as "port every Python function 1:1 into a PyO3-wrapped Rust
function" would almost certainly reproduce the same four rejections at
a much larger scale. A full rewrite done as "consolidate an entire
request's framework-side work into one Rust call, crossing into Python
only for the view function's own business logic" has a real chance of
winning, because it's structurally the same shape as the one candidate
that already did.

**Update from Phase 1 (see its entry below): the table above is all
microbenchmarks** (a Python op vs. the same op behind one FFI call, in
a tight `timeit` loop). Phase 1 tested the same tiny operation
(routing's dict lookup) a different way -- once per real HTTP request,
not in a loop -- and got a *different kind* of result: 0.95x-1.005x,
a null result, not a loss. That doesn't overturn the table above (a
tight loop really does pay the crossing cost every iteration, and that
cost is real), but it refines the thesis: at HTTP level, where a
request already pays for socket I/O and WSGI protocol handling
regardless, *one* FFI crossing's cost turned out to be noise-level even
for trivial work. The open question for Phase 3 onward isn't "can a
request afford one more Rust crossing" (Phase 1 suggests yes) -- it's
whether the Rust side does enough real computational work, once
there, to come out ahead on top of that.

**The ceiling this doesn't remove**: a view that runs real Python
business logic (most real applications) still has to cross back into
Python for that logic regardless of how much framework-side work moves
to Rust. `Python::attach`/`Python::detach` (PyO3 0.29's GIL
acquire/release, already used in `server.rs`'s connection-handling
loop and the signal dispatcher) manage this, but they don't eliminate
the crossing itself. This roadmap targets *framework* overhead, not
application code — it cannot make a slow view fast.

## What "full rewrite" means here

Python remains the language Hayavega's users write in: view functions,
`Settings`, `urlpatterns`, everything in a project's own `config/` and
app directories. **What moves to Rust is the framework's own internal
implementation** underneath that Python-facing API — the same
relationship `rust_runtime.py` already has to the WSGI server today
(Python calls `hayavega_native.serve_wsgi`; the framework around it,
including the WSGI application object itself, stays Python until later
phases migrate it too).

The public API surface (`Response`, `Request`'s fields, `path()`,
`Settings` shape, decorators) stays stable across every phase — a
project's own code should not need to change as phases land, the same
compatibility guarantee the existing `Settings.server.engine = 'rust'`
opt-in already gives.

## Execution principles (binding on every phase below)

1. **Vertical slices, not horizontal functions.** Each phase migrates
   a complete piece of the request path end-to-end into one (or a
   small number of) Rust call(s) — never "wrap this one existing
   Python function and call it from Python" in isolation.
2. **Benchmark the slice, not the microbenchmark.** `native_functions.py`-
   style microbenchmarks measure exactly the shape that's already
   failed four times. Each phase is gated on a *full HTTP-level*
   benchmark (the same methodology `rust_runtime_vs_wsgi.py` already
   uses: real subprocess servers, `oha`, the existing
   `json_echo`/`db_list`/`template_render`/`static_file` scenarios),
   because the whole thesis is amortized cost across a real request,
   which only a full-request benchmark can actually measure.
3. **The existing test suite is the acceptance bar.** ~7,913 lines of
   Python tests across `tests/*.py` must keep passing against each
   migrated phase, unmodified wherever the public API is unchanged.
   A phase that requires rewriting its own tests to pass is
   a phase that changed behavior, which needs to be an explicit,
   reviewed decision, not an incidental side effect.
4. **Every phase ships independently and reversibly**, feature-flagged
   the same way `Settings.server.engine = 'rust'` already is — never a
   single irreversible cutover. A phase that regresses gets disabled,
   not force-fixed under deadline pressure.
5. **A phase that benchmarks as a loss gets rejected and documented**,
   exactly like the four candidates already were — this roadmap
   commits to the discipline, not to a predetermined outcome for every
   phase.

## Phased plan

Ordered by dependency (later phases build on earlier ones) and by
where the project's own benchmarks already show the most request time
is actually spent (`db_list`/`template_render`'s ~35-50ms vs.
`json_echo`'s ~6-14ms — see `benchmarks/results/RESULTS.md`).

### Phase 0 — Foundations (prerequisite for everything below)
A shared Rust request-context type spanning routing match → handler
dispatch → response, so later phases compose into one call instead of
each inventing its own boundary; a vertical-slice benchmark harness
(wrapping the existing `oha`/subprocess methodology into a reusable
tool, not a new one per phase); explicit GIL-acquisition discipline
documented for contributors (`Python::attach` only at the points
where Python code — a view, a signal subscriber — is actually called;
everything else runs GIL-free).
**Size**: small in Rust lines, but the architectural decisions here
constrain every later phase — do not rush it.

### Phase 1 — Routing + request dispatch core ✅ done, null result, not wired in
`routing.py` (18 lines) plus the `_match()`/dispatch logic duplicated in
`runtime.py` and `asgi.py`. `hayavega_native.Router` built and benchmarked
at full HTTP level (not a microbenchmark -- see
`benchmarks/native_router_vs_python.py` and `benchmarks/README.md`'s
"A Rust router" section for the full result): **0.95x-1.005x average
across two runs, every scenario within normal run-to-run variance,
including `json_echo`, the scenario built to isolate routing
specifically.** Not wired in -- the gate requires a decisive win, and a
coin-flip result doesn't clear it -- but this is a genuine null result,
not a repeat of the four microbenchmark rejections' decisive losses.
**What it actually establishes**: a single FFI crossing per request is
close to free at HTTP level, so later phases' real question isn't
"can we afford to cross into Rust," it's "does the Rust side do enough
actual computational work once there" -- exactly what Phase 3 is
positioned to test. `Router` stays in `lib.rs`, unwired, as Phase 3's
starting point. The duplicate-`_match()`-call fix found along the way
(`runtime.py`/`asgi.py` call it twice per request, once for metrics
labeling) remains an open, Rust-independent cleanup, not yet applied to
the real framework code (only to the benchmark's own duplicated copy).

### Phase 2 — Request/Response core
`request.py` (125 lines, including multipart parsing) and
`response.py` (20 lines). These are the data types Phase 1 and every
later phase pass through, so they need to be Rust-native before DB/
template phases can avoid round-tripping through Python wrapper
objects.

### Phase 3 — Database execution ✅ done, narrowed, a real win, wired in
Originally scoped as the full `db.py`+`models.py` vertical slice
(connect → execute → materialize → serialize). Research before
building found that scope would likely have been a third null/loss
result: `Selection.values()` already goes straight to
`msgspec.json.encode()` with nothing for Rust to consolidate (Phase 4
was already folded in here and had nothing left to do), and `db.py`'s
own docstring already states the real cost is SQLite's own C-level
file I/O, "paid identically by both frameworks" -- not Python wrapper
overhead a rewrite removes. Narrowed twice, with the user's input each
time (see this repo's own session history), down to the one genuinely
different, untested architecture: **`hayavega_native.SerializedConnection`**
-- one Rust-owned read-only connection, every query serialized through
a single worker thread, instead of N threads each holding (or
pooling) their own. Modeled directly on `SignalDispatcher`'s existing
architecture (one `mpsc` channel, a dedicated OS thread calling back
into Python via `Python::attach`); no new Rust dependency -- the
worker thread calls Python's own `sqlite3` module, just through one
connection instead of several.

**Result: a real, decisive, reproducible win** -- the first since the
WSGI server/Signals dispatcher/`mango_list`. Benchmarked
(`benchmarks/serialized_connection_vs_python.py`) at full HTTP level
against both Python options for this exact question: **~1.5-1.7x
faster than the unpooled default, ~1.6x faster than
`enable_readonly_connection_pooling()`** (Python's own existing best
option, already independently confirmed via this same benchmark run's
built-in sanity check to be a null result against the unpooled
default, 0.97x-1.03x -- exactly matching what `db.py`'s own docstring
already documented, which also validates the benchmark methodology
itself). Two consecutive runs, consistent both times.

**Wired in** as `hayavega.db.SerializedConnection` -- a documented,
opt-in primitive (not integrated into `Selection`/`Entity`; call
`.execute(sql, params)` directly with raw SQL, get back an already-
materialized `list[dict]`), with a pure-Python fallback
(`threading.Lock` + one connection) when `hayavega_native` isn't built,
matching every other native feature's zero-configuration-required
convention. SQLite only, read-only only. Documented, with the
disclosed trade-off (every query now queues behind whichever one is
running), in `docs/topics/models.md` and `benchmarks/README.md`.
**Not** integrated into the ORM itself -- that remains a distinct,
larger, unscoped follow-up if more query shapes than this one
benchmarked case (`SELECT ... LIMIT ? OFFSET ?`) prove out the same
way.

### Phase 4 — Serialization: closed, no standalone work needed
`serializers.py` (84 lines). Already settled by research, not deferred:
`plain_value`'s standalone rejection showed why a bare object-graph
walk loses, and Phase 3's research additionally found `Selection.values()`
(the actual call `db_list` makes) never calls `_plain()`/any object-
graph walk at all -- it already goes dict → `msgspec.json.encode()`
directly, nothing to fold in. No further work here.

### Phase 5 — Template engine ❌ tried (as a general engine), rejected
`templates.py` (703 lines, the largest single pure-Python module). The
16-20x precedent (`render_post_list_page`) was always specifically the
*hardcoded*, one-template-only function -- what this phase would
actually need to ship is a *general* engine, and that candidate
already existed, fully built, before this session looked for it:
`benchmarks/native_template_engine.py` is a complete wire-format
bridge from `templates.py`'s real parsed node tree into Rust
`TemplateNode`/`TemplateExpr` (`crates/hayavega-core/src/lib.rs`),
correctness-verified byte-for-byte. Running it (for the first time
this session) gave a clear, decisive result:

| Candidate | Result |
|---|---:|
| `page.html` (variables + for, 20 rows) | Python 2.49x faster |
| `page.html`, wire-decode cached once | Python 2.93x faster |
| `if_heavy.html` (if/elif/else + for, 20 rows) | Python 2.62x faster |

Same structural reason as `_plain()`/the ASGI headers: real per-node
work, but still dominated by the FFI crossing cost at every
if/for/variable node rather than one crossing for the whole template
-- the same wall `_plain()`'s rejection already crossed, just not
checked here until it was. **Not pursued further.** See
`benchmarks/README.md`'s own section for the full writeup.

### Phase 6 — GraphQL ✅ done/win (lexer+parser only)
`graphql.py` (498 lines, a complete hand-rolled tokenizer/parser/
executor). Field *resolution* (`_resolve_root_field` calling arbitrary
Python resolvers) can never move to Rust -- that ceiling is real and
stayed in scope-out. But tokenizing and parsing a query string into a
parsed structure needs zero Python callbacks mid-parse, the same
"real work per crossing" shape the template-render win had.

Built as a faithful Rust port of `_tokenize`/`_Parser.parse_document()`
(`hayavega_native.parse_graphql_document`, same grammar, same rejected
constructs — directives, fragments) in `hayavega-core`/`hayavega-python`,
correctness-verified field-for-field against the real Python parser
before any timing. Measured (`benchmarks/native_graphql_parser.py`,
including the Python-side decode back into `_FieldNode`/
`_VariableDefinition`):

| Query shape | Result |
|---|---:|
| Small (`{ greeting }`) | **Native 2.71x faster** |
| Complex (variables, arguments, nested selections) | **Native 3.92x faster** |

A decisive, reproducible win -- wired into `Schema.execute()` as a
try-native-else-Python-fallback (`hayavega.graphql._parse_document`),
identical behavior and error messages on both paths. `execute()`'s
own logic past parsing (binding variables, depth/field-count limits,
calling resolvers, projecting results) is completely unchanged.

### Phase 7 — Authentication internals ❌ skipped, reasoned (not built)
`auth.py` (701 lines, the single largest module), read in full before
deciding. Every operation in it is SQL plus already-C crypto
(`scrypt`/`pbkdf2_hmac`, HMAC-SHA1 for TOTP) plus thin Python glue --
none of it has the "substantial, self-contained work per crossing"
shape the two real wins (`SerializedConnection`, the GraphQL parser)
share. The one candidate this entry originally flagged as plausible --
`authenticate_session`'s "validate token → look up user → check
permissions" vertical slice -- turns out, on inspection, to be a
single indexed-row SQL lookup plus a small JSON-list union: SQL does
the real work, Rust would just be paying the fixed FFI cost on trivial
glue around it, the same shape that already lost five times
(`compile_selection`, `normalize_path`, ASGI headers, `_plain()`, the
general template engine). Its own motivating condition -- "only if a
future multi-worker deployment needs genuinely shared, in-process
session state" -- also isn't a stated need today, just a hypothetical.
Not built; this is a reasoned skip, not an unrun guess, matching the
discipline every other rejected candidate got.

### Phase 8 — Remaining modules ❌ skipped, reasoned (not built)
Checked each area rather than assuming:

- **Admin** (`admin.py`, 496 lines) -- its list-page table renderer
  (`_render_list`) does hand-rolled per-row HTML escaping/formatting,
  structurally the *same shape* as `render_post_list_page`'s real win
  (substantial, batched work per call, not a trivial wrapped
  operation). Not pursued anyway: admin UI is low-traffic relative to
  API/data paths in a typical deployment, so the realistic throughput
  gain is small even if the microbenchmark would show a win -- the one
  candidate here where the skip is about deployment value, not
  structural infeasibility.
- **Forms** (`forms.py`, 204 lines) -- one factory function
  (`entity_form`) producing per-field validators; called once per
  submission, small per-field work, the trivial-wrapped-operation
  shape that loses.
- **Migrations** (`migrations.py`, 299 lines) -- DDL, not a
  per-request hot path at all; no throughput to win.
- **CLI/management/scaffold** (~500 lines combined) -- dev-time
  tooling, never in a production request path; Rust-izing it would add
  compile-time cost to contributor workflows for zero runtime benefit,
  regardless of what a benchmark would show (see "What stays Python
  regardless" below).

## What stays Python regardless of how far this goes

- Everything a project's own code writes: views, `Settings`, app
  modules, custom management commands, custom middleware factories.
- Dev-only tooling with no production request-path relevance: CLI,
  scaffolding, autoreload — Rust-izing these would add compile-time
  cost to contributor workflows for zero runtime benefit.
- Anything that fundamentally requires calling into arbitrary,
  unknown Python (signal subscribers, custom validators, custom cache
  key functions) — these cross the GIL boundary by definition,
  regardless of how much surrounding framework code is Rust.

## Timeline honesty

Originally estimated at 6-12+ months for a human team working at a
realistic pace, given ~8,864 lines of existing Python core logic to
match in behavior and ~7,913 lines of tests to keep green. Phases 0,
1, 3, and 4 (foundations through DB execution, the originally-flagged
highest-value target) shipped faster than that estimate implies in
practice -- each phase is still its own real PyO3 binding design,
GIL-discipline review, and full HTTP-level benchmark, and the
remaining phases (templates/GraphQL's real parsers, auth, admin) are
not smaller just because the first few moved quickly. Treat the
original estimate as directionally right for the phases still ahead,
not as disproven by how fast the first few went.

## Status: every phase resolved

| Phase | Result |
|---|---|
| 0 — Foundations | ✅ done |
| 1 — Routing | Null result (0.95x-1.005x at HTTP level) — not wired in |
| 2 — Request/Response core | Deprioritized — thin wrapper types, same logic as Phase 1's wash |
| 3 — DB execution | ✅ **Native 1.5-1.7x** — `SerializedConnection`, wired in |
| 4 — Serialization | Closed — nothing to fold in, confirmed via Phase 3's own research |
| 5 — Templates | ❌ Python 2.49-2.93x faster (general engine) — rejected |
| 6 — GraphQL lexer+parser | ✅ **Native 2.71-3.92x** — wired into `Schema.execute()` |
| 7 — Authentication internals | ❌ Skipped, reasoned — no candidate matches the winning shape |
| 8 — Admin/forms/migrations/CLI | ❌ Skipped, reasoned — see Phase 8's entry (admin is structurally plausible but low-value; the rest are trivial-shaped or dev-only) |

**Two real, decisive, reproducible wins out of nine phases** (`SerializedConnection`,
the GraphQL lexer+parser) — both because they do substantial,
self-contained work per Rust/Python FFI crossing, the same shape
`render_post_list_page` (this project's original native-server-era
win) already had. Every rejection shares the opposite shape: wrapping
one small operation while paying a fixed crossing cost with nothing to
amortize it against. This was never a "rewrite everything in Rust"
project in practice, despite the name — it became **benchmark-gated
selective migration**, which is exactly what the execution principles
at the top of this document asked for from the start.

## Next step

No further phase is queued. Revisit Phase 7 or Phase 8's admin
candidate only if a concrete, measured need arises (a profiler
pointing at `authenticate_session` or an admin list page under real
production load) — not speculatively. New candidates outside this
list should be held to the same gate every phase here was: benchmark
first, wire in only on a decisive win, document a loss as thoroughly
as a win.
