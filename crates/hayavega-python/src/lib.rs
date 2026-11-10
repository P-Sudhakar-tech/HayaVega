use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PySet, PyTuple};

use hayavega_core::{JsonValue, MangoList as CoreMangoList, TemplateExpr, TemplateNode};

use std::collections::HashMap;
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::thread;

mod server;

#[pyfunction]
fn normalize_path(path: &str) -> String {
    hayavega_core::normalize_path(path)
}

#[pyfunction]
#[pyo3(signature = (table, fields, conditions=vec![], ordering=vec![], limit=None, offset=0))]
fn compile_selection(
    table: String,
    fields: Vec<String>,
    conditions: Vec<(String, String)>,
    ordering: Vec<(String, String)>,
    limit: Option<usize>,
    offset: usize,
) -> PyResult<String> {
    hayavega_core::QueryPlan::new(table, fields, conditions, ordering, limit, offset)
        .map(|plan| plan.compile_select())
        .map_err(pyo3::exceptions::PyValueError::new_err)
}

fn graphql_value_to_py(py: Python<'_>, value: &hayavega_core::GraphQLValue) -> PyResult<Py<PyAny>> {
    use hayavega_core::GraphQLValue;
    match value {
        GraphQLValue::Null => Ok(py.None()),
        GraphQLValue::Bool(boolean) => Ok(boolean.into_pyobject(py)?.to_owned().into_any().unbind()),
        GraphQLValue::Int(integer) => Ok(integer.into_pyobject(py)?.into_any().unbind()),
        GraphQLValue::Float(number) => Ok(number.into_pyobject(py)?.into_any().unbind()),
        GraphQLValue::String(text) => Ok(text.into_pyobject(py)?.into_any().unbind()),
        // A 2-tuple tagged with "$" -- GraphQL list/object values are always Python
        // lists/dicts on this bridge, never tuples, so this cannot collide with a
        // real value. graphql.py's wire decoder looks for exactly this shape.
        GraphQLValue::Variable(name) => Ok(PyTuple::new(py, ["$".into_pyobject(py)?.into_any().unbind(), name.into_pyobject(py)?.into_any().unbind()])?.into_any().unbind()),
        GraphQLValue::List(items) => {
            let converted = items.iter().map(|item| graphql_value_to_py(py, item)).collect::<PyResult<Vec<_>>>()?;
            Ok(PyList::new(py, converted)?.into_any().unbind())
        }
        GraphQLValue::Object(fields) => {
            let result = PyDict::new(py);
            for (key, item) in fields {
                result.set_item(key, graphql_value_to_py(py, item)?)?;
            }
            Ok(result.into_any().unbind())
        }
    }
}

fn graphql_fields_to_py(py: Python<'_>, fields: &[hayavega_core::GraphQLField]) -> PyResult<Py<PyAny>> {
    let converted = fields
        .iter()
        .map(|field| -> PyResult<Py<PyAny>> {
            let arguments = field
                .arguments
                .iter()
                .map(|(name, value)| -> PyResult<Py<PyAny>> { Ok(PyTuple::new(py, [name.into_pyobject(py)?.into_any().unbind(), graphql_value_to_py(py, value)?])?.into_any().unbind()) })
                .collect::<PyResult<Vec<_>>>()?;
            let selection_set = match &field.selection_set {
                Some(sub_fields) => graphql_fields_to_py(py, sub_fields)?,
                None => py.None(),
            };
            Ok(PyTuple::new(py, [field.name.clone().into_pyobject(py)?.into_any().unbind(), PyList::new(py, arguments)?.into_any().unbind(), selection_set])?.into_any().unbind())
        })
        .collect::<PyResult<Vec<_>>>()?;
    Ok(PyList::new(py, converted)?.into_any().unbind())
}

/// Tokenizes and parses a GraphQL query, returning
/// `(operation_type, operation_name, variable_definitions, selection_set)` in
/// the wire format `src/hayavega/graphql.py`'s decoder expects. Mirrors
/// `_tokenize`/`_Parser.parse_document()` exactly -- field resolution itself
/// (arbitrary Python callables) stays entirely in Python.
#[pyfunction]
fn parse_graphql_document(py: Python<'_>, source: &str) -> PyResult<Py<PyAny>> {
    let document = hayavega_core::parse_graphql_document(source).map_err(pyo3::exceptions::PyValueError::new_err)?;
    let variable_definitions = document
        .variable_definitions
        .iter()
        .map(|definition| -> PyResult<Py<PyAny>> {
            let default = match &definition.default {
                Some(value) => graphql_value_to_py(py, value)?,
                None => py.None(),
            };
            Ok(PyTuple::new(py, [definition.name.clone().into_pyobject(py)?.into_any().unbind(), definition.required.into_pyobject(py)?.to_owned().into_any().unbind(), default, definition.has_default.into_pyobject(py)?.to_owned().into_any().unbind()])?.into_any().unbind())
        })
        .collect::<PyResult<Vec<_>>>()?;
    let operation_name = match &document.operation_name {
        Some(name) => name.into_pyobject(py)?.into_any().unbind(),
        None => py.None(),
    };
    Ok(PyTuple::new(
        py,
        [document.operation_type.into_pyobject(py)?.into_any().unbind(), operation_name, PyList::new(py, variable_definitions)?.into_any().unbind(), graphql_fields_to_py(py, &document.selection_set)?],
    )?
    .into_any()
    .unbind())
}

fn to_json_value(value: &Bound<'_, PyAny>) -> PyResult<JsonValue> {
    if value.is_none() { return Ok(JsonValue::Null); }
    if let Ok(boolean) = value.extract::<bool>() { return Ok(JsonValue::Bool(boolean)); }
    if let Ok(integer) = value.extract::<i64>() { return Ok(JsonValue::Number(integer.to_string())); }
    if let Ok(number) = value.extract::<f64>() {
        if !number.is_finite() { return Err(pyo3::exceptions::PyValueError::new_err("JSON numbers must be finite")); }
        return Ok(JsonValue::Number(number.to_string()));
    }
    if let Ok(text) = value.extract::<String>() { return Ok(JsonValue::String(text)); }
    if let Ok(items) = value.cast::<PyList>() {
        return Ok(JsonValue::Array(items.iter().map(|item| to_json_value(&item)).collect::<PyResult<Vec<_>>>()?));
    }
    if let Ok(items) = value.cast::<PyTuple>() {
        return Ok(JsonValue::Array(items.iter().map(|item| to_json_value(&item)).collect::<PyResult<Vec<_>>>()?));
    }
    if let Ok(mapping) = value.cast::<PyDict>() {
        let mut values = Vec::new();
        for (key, item) in mapping.iter() {
            values.push((key.extract::<String>()?, to_json_value(&item)?));
        }
        return Ok(JsonValue::Object(values));
    }
    Err(pyo3::exceptions::PyTypeError::new_err("Unsupported value for native JSON serialization"))
}

#[pyfunction]
fn serialize_json(value: Bound<'_, PyAny>) -> PyResult<String> {
    Ok(to_json_value(&value)?.encode())
}

/// Mirrors `hayavega.serializers._plain()` exactly: recursively reduce an
/// arbitrary value (an `Entity` with `.as_dict()`, a `Mapping`, a
/// `list`/`tuple`/`set`, a `date`/`datetime`, or anything else passed
/// through unchanged) to a plain Python structure `msgspec.json.encode`
/// can serialize. Returns a Python object graph, not a JSON string --
/// `_plain()`'s own result still goes through `msgspec.json.encode`
/// afterward, same as the pure-Python path.
///
/// Benchmarked, not wired in: `benchmarks/native_plain_value.py` measured
/// Python 1.33-1.59x faster across a flat dict, a single Entity, and a
/// 30-row Entity list -- the same per-node-FFI-crossing cost that already
/// sank `render_fragment_items`'s general case applies here too, since
/// this is a recursive Python-callback walk at every level. Kept
/// alongside `serialize_json`/`compile_selection`/`normalize_path` as a
/// documented, benchmarked rejection, not removed -- see
/// `UPCOMING_IMPROVEMENT_ROADMAP.md`.
fn plain_value_inner<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Py<PyAny>> {
    if value.hasattr("as_dict")? {
        let fields = value.call_method0("as_dict")?;
        let fields = fields.cast::<PyDict>().map_err(|_| pyo3::exceptions::PyTypeError::new_err("as_dict() must return a dict"))?;
        let result = PyDict::new(py);
        for (key, item) in fields.iter() {
            result.set_item(key, plain_value_inner(py, &item)?)?;
        }
        return Ok(result.into_any().unbind());
    }
    if let Ok(mapping) = value.cast::<PyDict>() {
        let result = PyDict::new(py);
        for (key, item) in mapping.iter() {
            result.set_item(key.str()?.to_string(), plain_value_inner(py, &item)?)?;
        }
        return Ok(result.into_any().unbind());
    }
    if let Ok(list) = value.cast::<PyList>() {
        let items = list.iter().map(|item| plain_value_inner(py, &item)).collect::<PyResult<Vec<_>>>()?;
        return Ok(PyList::new(py, items)?.into_any().unbind());
    }
    if let Ok(tuple) = value.cast::<PyTuple>() {
        let items = tuple.iter().map(|item| plain_value_inner(py, &item)).collect::<PyResult<Vec<_>>>()?;
        return Ok(PyList::new(py, items)?.into_any().unbind());
    }
    if let Ok(set) = value.cast::<PySet>() {
        let items = set.iter().map(|item| plain_value_inner(py, &item)).collect::<PyResult<Vec<_>>>()?;
        return Ok(PyList::new(py, items)?.into_any().unbind());
    }
    if value.hasattr("isoformat")? {
        return Ok(value.call_method0("isoformat")?.unbind());
    }
    Ok(value.clone().unbind())
}

#[pyfunction]
fn plain_value(py: Python<'_>, value: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    plain_value_inner(py, &value)
}

/// Benchmark-only: renders `benchmarks/hayavega_app/templates/page.html`'s exact
/// output for a list of (title, views) rows, as a best-case stand-in for a
/// native Rust template engine. Not used by the framework's real template
/// path (`hayavega.templates`) -- see `benchmarks/native_functions.py`.
#[pyfunction]
fn render_post_list_page(posts: Vec<(String, i64)>) -> String {
    hayavega_core::render_post_list_page(&posts)
}

/// Benchmark-only: renders the exact compact `<ul><li>...</li>...</ul>`
/// output `hayavega.ui`'s `ul(*(li(...) for ...))` component builders produce
/// for a list of (title, views) rows -- a hand-compiled, best-case stand-in
/// for a native Rust renderer of one specific htmx-swapped fragment shape.
/// See `benchmarks/native_htmx_fragment.py`.
#[pyfunction]
fn render_fragment_items(items: Vec<(String, i64)>) -> String {
    hayavega_core::render_fragment_items(&items)
}

/// Benchmark-only: mirrors `hayavega.asgi`'s request-header decoding
/// (lowercase ASGI byte-pairs -> Title-Case string pairs).
#[pyfunction]
fn decode_request_headers(headers: Vec<(Vec<u8>, Vec<u8>)>) -> Vec<(String, String)> {
    hayavega_core::decode_request_headers(&headers)
}

/// Benchmark-only: mirrors `hayavega.asgi`'s response-header encoding
/// (string pairs -> lowercase ASGI byte-pairs). Returns real `bytes` objects
/// (not lists of ints) so the output is usable as an actual ASGI header list.
#[pyfunction]
fn encode_response_headers<'py>(py: Python<'py>, headers: Vec<(String, String)>) -> Vec<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
    hayavega_core::encode_response_headers(&headers).into_iter().map(|(name, value)| (PyBytes::new(py, &name), PyBytes::new(py, &value))).collect()
}

fn wire_error(message: &str) -> PyErr {
    pyo3::exceptions::PyValueError::new_err(format!("malformed template wire format: {message}"))
}

fn wire_tag<'py>(tuple: &Bound<'py, PyTuple>) -> PyResult<String> {
    tuple.get_item(0)?.extract()
}

fn wire_to_expr(value: &Bound<'_, PyAny>) -> PyResult<TemplateExpr> {
    let tuple = value.cast::<PyTuple>().map_err(|_| wire_error("expected an expression tuple"))?;
    match wire_tag(tuple)?.as_str() {
        "lit" => Ok(TemplateExpr::Literal(to_json_value(&tuple.get_item(1)?)?)),
        "path" => Ok(TemplateExpr::Path(tuple.get_item(1)?.extract()?)),
        "not" => Ok(TemplateExpr::Not(Box::new(wire_to_expr(&tuple.get_item(1)?)?))),
        "cmp" => Ok(TemplateExpr::Compare(Box::new(wire_to_expr(&tuple.get_item(1)?)?), tuple.get_item(2)?.extract()?, Box::new(wire_to_expr(&tuple.get_item(3)?)?))),
        "bool" => {
            let op: String = tuple.get_item(1)?.extract()?;
            let operands = tuple.get_item(2)?;
            let operands = operands.cast::<PyTuple>().map_err(|_| wire_error("expected a tuple of boolean operands"))?;
            Ok(TemplateExpr::BoolOp(op, operands.iter().map(|item| wire_to_expr(&item)).collect::<PyResult<Vec<_>>>()?))
        }
        other => Err(wire_error(&format!("unknown expression tag {other:?}"))),
    }
}

fn wire_to_node(value: &Bound<'_, PyAny>) -> PyResult<TemplateNode> {
    let tuple = value.cast::<PyTuple>().map_err(|_| wire_error("expected a node tuple"))?;
    match wire_tag(tuple)?.as_str() {
        "text" => Ok(TemplateNode::Text(tuple.get_item(1)?.extract()?)),
        "var" => Ok(TemplateNode::Variable(wire_to_expr(&tuple.get_item(1)?)?)),
        "if" => {
            let branches = tuple.get_item(1)?;
            let branches = branches.cast::<PyTuple>().map_err(|_| wire_error("expected a tuple of if-branches"))?;
            let branches = branches
                .iter()
                .map(|branch| {
                    let branch = branch.cast::<PyTuple>().map_err(|_| wire_error("expected an (condition, body) branch tuple"))?;
                    let condition_item = branch.get_item(0)?;
                    let condition = if condition_item.is_none() { None } else { Some(wire_to_expr(&condition_item)?) };
                    Ok((condition, wire_to_nodes(&branch.get_item(1)?)?))
                })
                .collect::<PyResult<Vec<_>>>()?;
            Ok(TemplateNode::If(branches))
        }
        "for" => Ok(TemplateNode::For { var_name: tuple.get_item(1)?.extract()?, iterable: wire_to_expr(&tuple.get_item(2)?)?, body: wire_to_nodes(&tuple.get_item(3)?)? }),
        other => Err(wire_error(&format!("unknown node tag {other:?}"))),
    }
}

fn wire_to_nodes(value: &Bound<'_, PyAny>) -> PyResult<Vec<TemplateNode>> {
    let tuple = value.cast::<PyTuple>().map_err(|_| wire_error("expected a tuple of nodes"))?;
    tuple.iter().map(|item| wire_to_node(&item)).collect()
}

/// Render a template compiled to `hayavega.templates`'s wire tuple format (see
/// `benchmarks/native_template_engine.py`) against a JSON-safe context.
/// Decodes the wire tuples fresh on every call -- see `CompiledNativeTemplate`
/// for the cached-decode alternative used to isolate that cost in the
/// benchmark. Benchmark-only; not called from `hayavega.templates` today.
#[pyfunction]
fn render_compiled_template(nodes: Bound<'_, PyAny>, context: Bound<'_, PyAny>) -> PyResult<String> {
    let template_nodes = wire_to_nodes(&nodes)?;
    let context_value = to_json_value(&context)?;
    Ok(hayavega_core::render_template_nodes(&template_nodes, &context_value))
}

/// Benchmark-only: a template whose wire tuples are decoded into Rust
/// `TemplateNode`s exactly once (at construction, mirroring how
/// `hayavega.templates` already caches a compiled template per file), so
/// `render` only pays for context conversion and the actual interpretation
/// -- isolating that cost from wire-decoding cost.
#[pyclass(name = "CompiledNativeTemplate")]
struct PyCompiledNativeTemplate {
    nodes: Vec<TemplateNode>,
}

#[pymethods]
impl PyCompiledNativeTemplate {
    #[new]
    fn new(nodes: Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self { nodes: wire_to_nodes(&nodes)? })
    }

    fn render(&self, context: Bound<'_, PyAny>) -> PyResult<String> {
        let context_value = to_json_value(&context)?;
        Ok(hayavega_core::render_template_nodes(&self.nodes, &context_value))
    }
}

/// Mirrors `runtime.py`/`asgi.py`'s `_match()` exactly: an exact-pattern
/// lookup, falling back to a linear scan over routes whose pattern ends in
/// `/<path>` (the trailing-wildcard convention `routing.py` documents --
/// there is no `<id>`-style named capture group anywhere in this
/// framework's routing). Built once per application (mirroring
/// `create_application`'s own `routes = {...}`/`wildcard_routes = [...]`
/// construction at `runtime.py:130-131`), reused for every request.
///
/// Phase 1 of `RUST_CORE_REWRITE_ROADMAP.md`. Benchmarked at full HTTP
/// level, not as a microbenchmark -- see `benchmarks/native_router_vs_python.py`
/// and that file's own docstring for why a microbenchmark would just
/// reproduce `normalize_path`'s already-rejected result instead of testing
/// anything new.
#[pyclass(name = "Router")]
struct PyRouter {
    exact: HashMap<String, Py<PyAny>>,
    // (prefix, route) -- prefix already has the trailing "/<path>" suffix
    // stripped and a "/" appended, matching `item.pattern.removesuffix("/<path>") + "/"` exactly.
    wildcard: Vec<(String, Py<PyAny>)>,
}

#[pymethods]
impl PyRouter {
    /// `routes`: `(pattern, route_object)` pairs, in the same order
    /// `create_application`'s own `route_list` is -- a route whose pattern
    /// ends in `/<path>` is treated as a wildcard; every other pattern is
    /// an exact match, exactly mirroring `runtime.py:130-131`'s split.
    #[new]
    fn new(routes: Vec<(String, Py<PyAny>)>) -> Self {
        let mut exact = HashMap::with_capacity(routes.len());
        let mut wildcard = Vec::new();
        for (pattern, route) in routes {
            if let Some(stripped) = pattern.strip_suffix("/<path>") {
                wildcard.push((format!("{stripped}/"), route));
            } else {
                exact.insert(pattern, route);
            }
        }
        Self { exact, wildcard }
    }

    fn match_path(&self, py: Python<'_>, requested: &str) -> Option<Py<PyAny>> {
        if let Some(route) = self.exact.get(requested) {
            return Some(route.clone_ref(py));
        }
        self.wildcard.iter().find(|(prefix, _)| requested.starts_with(prefix.as_str())).map(|(_, route)| route.clone_ref(py))
    }
}

#[pyclass(name = "MangoList")]
struct PyMangoList {
    values: CoreMangoList<Py<PyAny>>,
}

fn normalized_index(index: isize, length: usize) -> PyResult<usize> {
    let result = if index < 0 { length as isize + index } else { index };
    if result < 0 || result as usize >= length {
        return Err(pyo3::exceptions::PyIndexError::new_err("mango_list index out of range"));
    }
    Ok(result as usize)
}

#[pymethods]
impl PyMangoList {
    #[new]
    #[pyo3(signature = (items=None))]
    fn new(py: Python<'_>, items: Option<Bound<'_, PyAny>>) -> PyResult<Self> {
        let _ = py;
        let values = match items {
            Some(items) => items.try_iter()?.map(|item| item.map(|value| value.unbind())).collect::<PyResult<Vec<_>>>()?,
            None => Vec::new(),
        };
        Ok(Self { values: CoreMangoList::new(values) })
    }

    fn append(&mut self, value: Py<PyAny>) { self.values.append(value); }
    fn clear(&mut self) { self.values.clear(); }
    fn __len__(&self) -> usize { self.values.len() }
    fn __bool__(&self) -> bool { !self.values.is_empty() }
    fn __getitem__(&self, py: Python<'_>, index: isize) -> PyResult<Py<PyAny>> { Ok(self.values.as_slice()[normalized_index(index, self.values.len())?].clone_ref(py)) }
    fn __setitem__(&mut self, index: isize, value: Py<PyAny>) -> PyResult<()> {
        let position = normalized_index(index, self.values.len())?;
        self.values.as_mut_slice()[position] = value;
        Ok(())
    }
    #[pyo3(signature = (index=None))]
    fn pop(&mut self, index: Option<isize>) -> PyResult<Py<PyAny>> {
        let position = match index { Some(value) => normalized_index(value, self.values.len())?, None if self.values.is_empty() => return Err(pyo3::exceptions::PyIndexError::new_err("pop from empty mango_list")), None => self.values.len() - 1 };
        Ok(self.values.remove(position))
    }
    fn to_list(&self, py: Python<'_>) -> PyResult<Py<PyAny>> { Ok(PyList::new(py, self.values.as_slice())?.into_any().unbind()) }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> { Ok(format!("mango_list({})", self.to_list(py)?.bind(py).repr()?)) }
}

/// One event delivery: a fixed subscriber snapshot plus the event to hand each of them.
struct SignalJob {
    subscribers: Vec<Py<PyAny>>,
    event: Py<PyAny>,
}

fn run_signal_job(job: SignalJob) {
    Python::attach(|py| {
        for subscriber in &job.subscribers {
            if let Err(error) = subscriber.call1(py, (job.event.clone_ref(py),)) {
                // A subscriber failure must never stop later subscribers or the
                // worker thread; log it the same way the Python fallback does.
                if let Ok(logging) = py.import("logging") {
                    if let Ok(logger) = logging.call_method1("getLogger", ("hayavega.signals",)) {
                        let _ = logger.call_method1("error", (format!("Signal subscriber failed: {error}"),));
                    }
                }
            }
        }
    });
}

/// A small persistent OS-thread pool that delivers Signals events off the caller's thread.
///
/// Jobs queue on a single channel shared by a fixed number of native worker
/// threads (not Python's ``threading`` module), so delivery never blocks the
/// publisher and never spawns a new thread per event.
#[pyclass(name = "SignalDispatcher")]
struct PySignalDispatcher {
    sender: Option<Sender<SignalJob>>,
}

#[pymethods]
impl PySignalDispatcher {
    #[new]
    #[pyo3(signature = (workers=4))]
    fn new(workers: usize) -> Self {
        let (sender, receiver) = channel::<SignalJob>();
        let receiver = Arc::new(Mutex::new(receiver));
        for _ in 0..workers.max(1) {
            let receiver = Arc::clone(&receiver);
            thread::spawn(move || loop {
                let job = receiver.lock().expect("Signal dispatcher queue lock was poisoned").recv();
                match job {
                    Ok(job) => run_signal_job(job),
                    Err(_) => break,
                }
            });
        }
        Self { sender: Some(sender) }
    }

    /// Queue ``event`` for delivery to ``subscribers`` and return immediately.
    fn dispatch(&self, subscribers: Vec<Py<PyAny>>, event: Py<PyAny>) -> PyResult<()> {
        match &self.sender {
            Some(sender) => sender
                .send(SignalJob { subscribers, event })
                .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("Signal dispatcher workers are no longer running")),
            None => Err(pyo3::exceptions::PyRuntimeError::new_err("Signal dispatcher is closed")),
        }
    }

    /// Drop the sender so idle workers exit; called explicitly or at garbage collection.
    fn close(&mut self) {
        self.sender = None;
    }
}

/// One bound parameter, converted from a Python value to a form rusqlite
/// can bind without needing the GIL again once the worker actually runs
/// the query. Covers every type this project's own call sites pass
/// (`int`/`str`/`float`/`bytes`/`None`/`bool`) -- not a general Python-to-
/// SQLite marshaler, since `SerializedConnection.execute()` is documented
/// as accepting a plain parameter tuple for a raw SQL string, not
/// arbitrary Python objects.
#[derive(Clone)]
enum BoundParam {
    Null,
    Integer(i64),
    Real(f64),
    Text(String),
    Blob(Vec<u8>),
}

impl rusqlite::types::ToSql for BoundParam {
    fn to_sql(&self) -> rusqlite::Result<rusqlite::types::ToSqlOutput<'_>> {
        use rusqlite::types::{ToSqlOutput, Value};
        Ok(match self {
            BoundParam::Null => ToSqlOutput::Owned(Value::Null),
            BoundParam::Integer(v) => ToSqlOutput::Owned(Value::Integer(*v)),
            BoundParam::Real(v) => ToSqlOutput::Owned(Value::Real(*v)),
            BoundParam::Text(v) => ToSqlOutput::Owned(Value::Text(v.clone())),
            BoundParam::Blob(v) => ToSqlOutput::Owned(Value::Blob(v.clone())),
        })
    }
}

fn python_param_to_bound(value: &Bound<'_, PyAny>) -> PyResult<BoundParam> {
    if value.is_none() {
        Ok(BoundParam::Null)
    } else if let Ok(v) = value.extract::<bool>() {
        Ok(BoundParam::Integer(v as i64))
    } else if let Ok(v) = value.extract::<i64>() {
        Ok(BoundParam::Integer(v))
    } else if let Ok(v) = value.extract::<f64>() {
        Ok(BoundParam::Real(v))
    } else if let Ok(v) = value.extract::<Vec<u8>>() {
        Ok(BoundParam::Blob(v))
    } else if let Ok(v) = value.extract::<String>() {
        Ok(BoundParam::Text(v))
    } else {
        Err(pyo3::exceptions::PyTypeError::new_err(format!("SerializedConnection.execute() cannot bind parameter of type {}", value.get_type().name()?)))
    }
}

/// One already-fetched result row: column name paired with its value,
/// converted from SQLite's own type system to a Python-ready form while
/// still off the GIL -- only the final `PyDict`/`PyList` construction
/// needs Python at all.
enum CellValue {
    Null,
    Integer(i64),
    Real(f64),
    Text(String),
    Blob(Vec<u8>),
}

fn cell_to_python<'py>(py: Python<'py>, value: &CellValue) -> Bound<'py, PyAny> {
    match value {
        CellValue::Null => py.None().into_bound(py),
        CellValue::Integer(v) => v.into_pyobject(py).unwrap().into_any(),
        CellValue::Real(v) => v.into_pyobject(py).unwrap().into_any(),
        CellValue::Text(v) => v.into_pyobject(py).unwrap().into_any(),
        CellValue::Blob(v) => PyBytes::new(py, v).into_any(),
    }
}

/// One query request, and the channel its result comes back on.
struct QueryJob {
    sql: String,
    params: Py<PyAny>,
    respond_to: Sender<PyResult<Py<PyAny>>>,
}

/// Runs the query entirely through `rusqlite` -- a native Rust SQLite
/// driver, not Python's own `sqlite3` module -- so the worker thread does
/// not need the GIL at all for the actual statement preparation,
/// execution, and row fetching, only for reading the incoming parameter
/// tuple beforehand and building the resulting `list[dict]` afterward.
/// Replaces an earlier version that called back into Python's `sqlite3`
/// module for every query *and* called Python's `dict()` constructor once
/// per returned row -- measured (`benchmarks/serialized_connection_vs_python.py`
/// and direct `cProfile` profiling of `benchmarks/hayavega_app/app.py`'s
/// `/posts.json`) at ~140-220us/call for a trivial 20-row `SELECT`, the
/// single largest cost in that route's entire request. See
/// `RUST_CORE_REWRITE_ROADMAP.md` for the benchmarked result.
fn run_query_job(connection: &Mutex<rusqlite::Connection>, job: QueryJob) {
    let bound_params: PyResult<Vec<BoundParam>> = Python::attach(|py| {
        let tuple = job.params.bind(py).cast::<PyTuple>().map_err(|_| pyo3::exceptions::PyTypeError::new_err("SerializedConnection.execute() params must be a tuple"))?;
        tuple.iter().map(|item| python_param_to_bound(&item)).collect()
    });
    let result = (|| -> PyResult<(Vec<String>, Vec<Vec<CellValue>>)> {
        let bound_params = bound_params?;
        let conn = connection.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let mut statement = conn.prepare(&job.sql).map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))?;
        let column_names: Vec<String> = statement.column_names().into_iter().map(String::from).collect();
        let param_refs: Vec<&dyn rusqlite::types::ToSql> = bound_params.iter().map(|p| p as &dyn rusqlite::types::ToSql).collect();
        let rows = statement
            .query_map(param_refs.as_slice(), |row| {
                let mut cells = Vec::with_capacity(column_names.len());
                for index in 0..column_names.len() {
                    let cell = match row.get_ref(index)? {
                        rusqlite::types::ValueRef::Null => CellValue::Null,
                        rusqlite::types::ValueRef::Integer(v) => CellValue::Integer(v),
                        rusqlite::types::ValueRef::Real(v) => CellValue::Real(v),
                        rusqlite::types::ValueRef::Text(v) => CellValue::Text(String::from_utf8_lossy(v).into_owned()),
                        rusqlite::types::ValueRef::Blob(v) => CellValue::Blob(v.to_vec()),
                    };
                    cells.push(cell);
                }
                Ok(cells)
            })
            .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))?;
        let mut materialized_rows = Vec::new();
        for row in rows {
            materialized_rows.push(row.map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))?);
        }
        Ok((column_names, materialized_rows))
    })();
    // Each column-name string is converted to a Python object exactly once
    // per query and reused across every row's dict -- not once per
    // (row, column) pair, which `PyDict::set_item(&String, ...)` would do
    // if called with a fresh Rust `String` reference each time. Profiling
    // found this interning absent entirely: a 20-row result paid for 60
    // fresh `PyUnicode` allocations (3 columns x 20 rows) instead of 3,
    // the single largest remaining cost in `db_list`/`template_render`
    // after switching this type to `rusqlite`.
    let final_result = result.and_then(|(column_names, rows)| {
        Python::attach(|py| -> PyResult<Py<PyAny>> {
            let keys: Vec<_> = column_names.iter().map(|name| name.into_pyobject(py).unwrap()).collect();
            let result_list = PyList::empty(py);
            for cells in &rows {
                let dict = PyDict::new(py);
                for (key, cell) in keys.iter().zip(cells.iter()) {
                    dict.set_item(key, cell_to_python(py, cell))?;
                }
                result_list.append(dict)?;
            }
            Ok(result_list.into_any().unbind())
        })
    });
    let _ = job.respond_to.send(final_result);
}

/// Phase 3 of `RUST_CORE_REWRITE_ROADMAP.md`: one read-only SQLite
/// connection, owned by a single dedicated native thread, serializing
/// every query through one `mpsc` channel instead of each caller thread
/// holding its own connection -- the genuinely different architecture
/// from Python's existing per-thread pooling (see `db.py`'s
/// `enable_readonly_connection_pooling`), not a Rust port of that pool's
/// bookkeeping.
///
/// **Uses `rusqlite` (a native Rust SQLite driver), not Python's own
/// `sqlite3` module.** An earlier version called back into Python's
/// `sqlite3` for every query and called Python's `dict()` constructor
/// once per returned row -- profiling found that cross-GIL round trip
/// costing ~140-220us for a trivial 20-row `SELECT` on this codebase's
/// own benchmark app, the single largest cost in that route's entire
/// request (see `RUST_CORE_REWRITE_ROADMAP.md`'s benchmarked result and
/// `benchmarks/serialized_connection_vs_python.py`). The worker thread
/// now does statement preparation, execution, and row fetching entirely
/// off the GIL; it attaches to Python only twice per query, briefly: once
/// to read the incoming parameter tuple before releasing the GIL to run
/// the query, once to build the resulting `list[dict]` from already-
/// materialized Rust values afterward.
///
/// `execute()` releases the GIL (`py.detach`) while waiting for the
/// worker's response, same as before this change -- not because the
/// worker needs it back (it mostly doesn't, now), but so the calling
/// thread doesn't block other Python threads while it waits.
#[pyclass(name = "SerializedConnection")]
struct PySerializedConnection {
    sender: Option<Sender<QueryJob>>,
    worker: Option<thread::JoinHandle<()>>,
}

#[pymethods]
impl PySerializedConnection {
    #[new]
    fn new(path: String) -> PyResult<Self> {
        let uri = format!("file:{path}?mode=ro");
        let flags = rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX;
        let raw_connection = rusqlite::Connection::open_with_flags(&uri, flags).map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))?;
        let connection = Mutex::new(raw_connection);

        let (sender, receiver) = channel::<QueryJob>();
        let worker = thread::spawn(move || {
            while let Ok(job) = receiver.recv() {
                run_query_job(&connection, job);
            }
            // connection (and the rusqlite::Connection it wraps) drops here,
            // closing the file handle deterministically once the channel is
            // drained -- close() below joins this thread before returning,
            // so the handle is guaranteed released by the time it does.
        });
        Ok(Self { sender: Some(sender), worker: Some(worker) })
    }

    fn execute(&self, py: Python<'_>, sql: String, params: Py<PyAny>) -> PyResult<Py<PyAny>> {
        let Some(sender) = &self.sender else {
            return Err(pyo3::exceptions::PyRuntimeError::new_err("SerializedConnection is closed"));
        };
        let (respond_to, response) = channel();
        sender
            .send(QueryJob { sql, params, respond_to })
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("SerializedConnection worker is no longer running"))?;
        py.detach(move || response.recv())
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("SerializedConnection worker is no longer running"))?
    }

    /// Stop the worker thread and close the underlying connection,
    /// blocking until it has actually finished -- not just signaled to
    /// stop -- so the SQLite file handle is guaranteed released by the
    /// time this returns. Safe to call more than once; `execute()` after
    /// `close()` raises clearly rather than hanging.
    fn close(&mut self, py: Python<'_>) {
        self.sender = None;
        if let Some(worker) = self.worker.take() {
            py.detach(move || {
                let _ = worker.join();
            });
        }
    }
}

#[pymodule]
fn hayavega_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(normalize_path, module)?)?;
    module.add_function(wrap_pyfunction!(compile_selection, module)?)?;
    module.add_function(wrap_pyfunction!(parse_graphql_document, module)?)?;
    module.add_function(wrap_pyfunction!(serialize_json, module)?)?;
    module.add_function(wrap_pyfunction!(plain_value, module)?)?;
    module.add_function(wrap_pyfunction!(render_post_list_page, module)?)?;
    module.add_function(wrap_pyfunction!(render_fragment_items, module)?)?;
    module.add_function(wrap_pyfunction!(decode_request_headers, module)?)?;
    module.add_function(wrap_pyfunction!(encode_response_headers, module)?)?;
    module.add_function(wrap_pyfunction!(render_compiled_template, module)?)?;
    module.add_class::<PyCompiledNativeTemplate>()?;
    module.add_class::<PyRouter>()?;
    module.add_class::<PyMangoList>()?;
    module.add_class::<PySignalDispatcher>()?;
    module.add_class::<PySerializedConnection>()?;
    server::register(module)?;
    Ok(())
}
