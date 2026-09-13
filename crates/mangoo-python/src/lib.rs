use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyTuple};

use mangoo_core::{JsonValue, MangoList as CoreMangoList, TemplateExpr, TemplateNode};

use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::thread;

#[pyfunction]
fn normalize_path(path: &str) -> String {
    mangoo_core::normalize_path(path)
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
    mangoo_core::QueryPlan::new(table, fields, conditions, ordering, limit, offset)
        .map(|plan| plan.compile_select())
        .map_err(pyo3::exceptions::PyValueError::new_err)
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
    if let Ok(items) = value.downcast::<PyList>() {
        return Ok(JsonValue::Array(items.iter().map(|item| to_json_value(&item)).collect::<PyResult<Vec<_>>>()?));
    }
    if let Ok(items) = value.downcast::<PyTuple>() {
        return Ok(JsonValue::Array(items.iter().map(|item| to_json_value(&item)).collect::<PyResult<Vec<_>>>()?));
    }
    if let Ok(mapping) = value.downcast::<PyDict>() {
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

/// Benchmark-only: renders `benchmarks/mangoo_app/templates/page.html`'s exact
/// output for a list of (title, views) rows, as a best-case stand-in for a
/// native Rust template engine. Not used by the framework's real template
/// path (`mangoo.templates`) -- see `benchmarks/native_functions.py`.
#[pyfunction]
fn render_post_list_page(posts: Vec<(String, i64)>) -> String {
    mangoo_core::render_post_list_page(&posts)
}

/// Benchmark-only: renders the exact compact `<ul><li>...</li>...</ul>`
/// output `mangoo.ui`'s `ul(*(li(...) for ...))` component builders produce
/// for a list of (title, views) rows -- a hand-compiled, best-case stand-in
/// for a native Rust renderer of one specific htmx-swapped fragment shape.
/// See `benchmarks/native_htmx_fragment.py`.
#[pyfunction]
fn render_fragment_items(items: Vec<(String, i64)>) -> String {
    mangoo_core::render_fragment_items(&items)
}

/// Benchmark-only: mirrors `mangoo.asgi`'s request-header decoding
/// (lowercase ASGI byte-pairs -> Title-Case string pairs).
#[pyfunction]
fn decode_request_headers(headers: Vec<(Vec<u8>, Vec<u8>)>) -> Vec<(String, String)> {
    mangoo_core::decode_request_headers(&headers)
}

/// Benchmark-only: mirrors `mangoo.asgi`'s response-header encoding
/// (string pairs -> lowercase ASGI byte-pairs). Returns real `bytes` objects
/// (not lists of ints) so the output is usable as an actual ASGI header list.
#[pyfunction]
fn encode_response_headers<'py>(py: Python<'py>, headers: Vec<(String, String)>) -> Vec<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
    mangoo_core::encode_response_headers(&headers).into_iter().map(|(name, value)| (PyBytes::new_bound(py, &name), PyBytes::new_bound(py, &value))).collect()
}

fn wire_error(message: &str) -> PyErr {
    pyo3::exceptions::PyValueError::new_err(format!("malformed template wire format: {message}"))
}

fn wire_tag<'py>(tuple: &Bound<'py, PyTuple>) -> PyResult<String> {
    tuple.get_item(0)?.extract()
}

fn wire_to_expr(value: &Bound<'_, PyAny>) -> PyResult<TemplateExpr> {
    let tuple = value.downcast::<PyTuple>().map_err(|_| wire_error("expected an expression tuple"))?;
    match wire_tag(tuple)?.as_str() {
        "lit" => Ok(TemplateExpr::Literal(to_json_value(&tuple.get_item(1)?)?)),
        "path" => Ok(TemplateExpr::Path(tuple.get_item(1)?.extract()?)),
        "not" => Ok(TemplateExpr::Not(Box::new(wire_to_expr(&tuple.get_item(1)?)?))),
        "cmp" => Ok(TemplateExpr::Compare(Box::new(wire_to_expr(&tuple.get_item(1)?)?), tuple.get_item(2)?.extract()?, Box::new(wire_to_expr(&tuple.get_item(3)?)?))),
        "bool" => {
            let op: String = tuple.get_item(1)?.extract()?;
            let operands = tuple.get_item(2)?;
            let operands = operands.downcast::<PyTuple>().map_err(|_| wire_error("expected a tuple of boolean operands"))?;
            Ok(TemplateExpr::BoolOp(op, operands.iter().map(|item| wire_to_expr(&item)).collect::<PyResult<Vec<_>>>()?))
        }
        other => Err(wire_error(&format!("unknown expression tag {other:?}"))),
    }
}

fn wire_to_node(value: &Bound<'_, PyAny>) -> PyResult<TemplateNode> {
    let tuple = value.downcast::<PyTuple>().map_err(|_| wire_error("expected a node tuple"))?;
    match wire_tag(tuple)?.as_str() {
        "text" => Ok(TemplateNode::Text(tuple.get_item(1)?.extract()?)),
        "var" => Ok(TemplateNode::Variable(wire_to_expr(&tuple.get_item(1)?)?)),
        "if" => {
            let branches = tuple.get_item(1)?;
            let branches = branches.downcast::<PyTuple>().map_err(|_| wire_error("expected a tuple of if-branches"))?;
            let branches = branches
                .iter()
                .map(|branch| {
                    let branch = branch.downcast::<PyTuple>().map_err(|_| wire_error("expected an (condition, body) branch tuple"))?;
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
    let tuple = value.downcast::<PyTuple>().map_err(|_| wire_error("expected a tuple of nodes"))?;
    tuple.iter().map(|item| wire_to_node(&item)).collect()
}

/// Render a template compiled to `mangoo.templates`'s wire tuple format (see
/// `benchmarks/native_template_engine.py`) against a JSON-safe context.
/// Decodes the wire tuples fresh on every call -- see `CompiledNativeTemplate`
/// for the cached-decode alternative used to isolate that cost in the
/// benchmark. Benchmark-only; not called from `mangoo.templates` today.
#[pyfunction]
fn render_compiled_template(nodes: Bound<'_, PyAny>, context: Bound<'_, PyAny>) -> PyResult<String> {
    let template_nodes = wire_to_nodes(&nodes)?;
    let context_value = to_json_value(&context)?;
    Ok(mangoo_core::render_template_nodes(&template_nodes, &context_value))
}

/// Benchmark-only: a template whose wire tuples are decoded into Rust
/// `TemplateNode`s exactly once (at construction, mirroring how
/// `mangoo.templates` already caches a compiled template per file), so
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
        Ok(mangoo_core::render_template_nodes(&self.nodes, &context_value))
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
            Some(items) => items.iter()?.map(|item| item.map(|value| value.unbind())).collect::<PyResult<Vec<_>>>()?,
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
    fn to_list(&self, py: Python<'_>) -> PyResult<Py<PyAny>> { Ok(PyList::new_bound(py, self.values.as_slice()).into_any().unbind()) }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> { Ok(format!("mango_list({})", self.to_list(py)?.bind(py).repr()?)) }
}

/// One event delivery: a fixed subscriber snapshot plus the event to hand each of them.
struct SignalJob {
    subscribers: Vec<Py<PyAny>>,
    event: Py<PyAny>,
}

fn run_signal_job(job: SignalJob) {
    Python::with_gil(|py| {
        for subscriber in &job.subscribers {
            if let Err(error) = subscriber.call1(py, (job.event.clone_ref(py),)) {
                // A subscriber failure must never stop later subscribers or the
                // worker thread; log it the same way the Python fallback does.
                if let Ok(logging) = py.import_bound("logging") {
                    if let Ok(logger) = logging.call_method1("getLogger", ("mangoo.signals",)) {
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

#[pymodule]
fn mangoo_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(normalize_path, module)?)?;
    module.add_function(wrap_pyfunction!(compile_selection, module)?)?;
    module.add_function(wrap_pyfunction!(serialize_json, module)?)?;
    module.add_function(wrap_pyfunction!(render_post_list_page, module)?)?;
    module.add_function(wrap_pyfunction!(render_fragment_items, module)?)?;
    module.add_function(wrap_pyfunction!(decode_request_headers, module)?)?;
    module.add_function(wrap_pyfunction!(encode_response_headers, module)?)?;
    module.add_function(wrap_pyfunction!(render_compiled_template, module)?)?;
    module.add_class::<PyCompiledNativeTemplate>()?;
    module.add_class::<PyMangoList>()?;
    module.add_class::<PySignalDispatcher>()?;
    Ok(())
}
