# Rust core rewrite — a phased roadmap

**Status: scoped, not started.** This document is the deliverable from
an explicit decision to pursue a full Rust rewrite of Mangoo's core,
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

**The ceiling this doesn't remove**: a view that runs real Python
business logic (most real applications) still has to cross back into
Python for that logic regardless of how much framework-side work moves
to Rust. `Python::attach`/`Python::detach` (PyO3 0.29's GIL
acquire/release, already used in `server.rs`'s connection-handling
loop and the signal dispatcher) manage this, but they don't eliminate
the crossing itself. This roadmap targets *framework* overhead, not
application code — it cannot make a slow view fast.

## What "full rewrite" means here

Python remains the language Mangoo's users write in: view functions,
`Settings`, `urlpatterns`, everything in a project's own `config/` and
app directories. **What moves to Rust is the framework's own internal
implementation** underneath that Python-facing API — the same
relationship `rust_runtime.py` already has to the WSGI server today
(Python calls `mangoo_native.serve_wsgi`; the framework around it,
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

### Phase 1 — Routing + request dispatch core
`routing.py` (18 lines) plus the `_match()`/dispatch logic currently
duplicated in `runtime.py` and `asgi.py` (~208 + 383 lines, not all
dispatch-related). Smallest standalone piece, and everything else
depends on having a route resolved before it can do anything.
**Vertical-slice shape**: parse the incoming path once, match it,
return the resolved handler reference to Python in one call — not
"accelerate the dict lookup" (already shown to lose on its own).

### Phase 2 — Request/Response core
`request.py` (125 lines, including multipart parsing) and
`response.py` (20 lines). These are the data types Phase 1 and every
later phase pass through, so they need to be Rust-native before DB/
template phases can avoid round-tripping through Python wrapper
objects.

### Phase 3 — Database execution + ORM (highest-value target)
`db.py` (435 lines) + `models.py` (419 lines). This is where the
project's own benchmarks show the real time goes
(`db_list`/`template_render` costing 4-6x `json_echo`). **This is the
one phase most likely to actually win**, because it's naturally a
vertical slice already: connect → execute → materialize rows →
return — do all of it in one Rust call (including what `_plain()`/
serialization would otherwise do as a separate crossing, folding
Phase 4 into this call rather than chaining two FFI crossings).
Must preserve: the existing connection-pooling behavior and its
documented WSGI-regression/ASGI-win split (`db.py:135-166`'s
benchmark-backed warning), the stale-connection retry logic, and the
`_PREPARED_TABLES`-style caching this session's own `auth.py` fix just
added a second instance of.

### Phase 4 — Serialization (folded into Phase 3, not standalone)
`serializers.py` (84 lines). Explicitly **not** a standalone phase —
this session's `plain_value` rejection showed exactly why a standalone
object-graph-walk function loses. Only pursue this as part of Phase
3's single DB-to-JSON call, where the serialization work is amortized
against the DB execution crossing instead of paying its own.

### Phase 5 — Template engine
`templates.py` (703 lines, the largest single pure-Python module).
Builds on an already-proven result: the hardcoded `render_post_list_page`
candidate won 16-20x (`benchmarks/README.md`'s second native-function
benchmark) by doing real per-row work in one call — but it only
handles one exact template shape. `UPCOMING_IMPROVEMENT_ROADMAP.md`
already flags "a genuine Rust template compiler" as a scoped follow-up
with a measured ceiling to justify it; this phase is that follow-up,
built properly: full `{% if %}`/`{% for %}`/`{% extends %}`/`{% block %}`
support, not a hand-compiled single-template stand-in. Highest
implementation complexity of any phase (a real parser + compiler, not
a wrapped data transform).

### Phase 6 — GraphQL
`graphql.py` (498 lines, a complete hand-rolled tokenizer/parser/
executor). A plausible winner unlike the four rejected candidates:
parsing and executing a whole query is substantial, non-trivial work
per call, the same "real work per crossing" shape the template-render
win had. Lower priority than Phases 1-5 since GraphQL is one API
surface among several (REST, admin, templates), not every request's
path.

### Phase 7 — Authentication internals
`auth.py` (701 lines, the single largest module). State upfront,
honestly: the actual expensive operations (`scrypt`/`pbkdf2_hmac` via
`hashlib`, HMAC-SHA1 for TOTP) are already C/OpenSSL calls — this
phase will not speed up password hashing itself, by design (scrypt is
*supposed* to be slow). What's worth migrating is the surrounding
session/permission-check logic as a vertical slice (validate token →
look up user → check permissions, one call), and only if a future
multi-worker deployment needs genuinely shared, in-process session
state beyond what the existing `_INITIALIZED_AUTH_DBS` cache (this
session's fix) already provides.

### Phase 8 — Remaining modules (lowest priority, smallest value)
Admin (`admin.py`, 496 lines — UI rendering, typically low request
volume relative to API/data paths), forms (`forms.py`, 204 lines),
migrations (`migrations.py`, 299 lines — not a per-request hot path at
all), CLI/management/scaffold (~500 lines combined — dev-time tooling,
never in a production request path). Revisit only after Phases 1-7
are shipped and measured; these are not where request time goes.

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

This is a multi-month, likely 6-12+ month effort at a realistic pace,
given ~8,864 lines of existing Python core logic to match in
behavior, ~7,913 lines of tests to keep green, and each phase needing
its own PyO3 binding design, GIL-discipline review, and full
HTTP-level benchmark — not a rewrite that fits in further single
sessions. Phase 0-3 (foundations through DB execution, the
highest-value target) is the realistic first milestone; everything
past that depends on what Phase 3's actual measured result is.

## Next step

Phase 0 + Phase 1 design, in a dedicated follow-up: the shared request-
context type, the vertical-slice benchmark harness, and routing/
dispatch migrated as the first proof that the "bigger unit of work per
crossing" thesis actually holds at HTTP level, not just in theory.
Everything past that is sequenced on what that first measurement
shows.
