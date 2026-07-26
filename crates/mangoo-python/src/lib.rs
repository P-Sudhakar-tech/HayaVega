use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};

use mangoo_core::{JsonValue, MangoList as CoreMangoList};

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
    fn __getitem__(&self, index: isize) -> PyResult<Py<PyAny>> { Ok(self.values.as_slice()[normalized_index(index, self.values.len())?].clone()) }
    fn __setitem__(&mut self, index: isize, value: Py<PyAny>) -> PyResult<()> {
        let position = normalized_index(index, self.values.len())?;
        self.values.as_mut_slice()[position] = value;
        Ok(())
    }
    fn pop(&mut self, index: Option<isize>) -> PyResult<Py<PyAny>> {
        let position = match index { Some(value) => normalized_index(value, self.values.len())?, None if self.values.is_empty() => return Err(pyo3::exceptions::PyIndexError::new_err("pop from empty mango_list")), None => self.values.len() - 1 };
        Ok(self.values.remove(position))
    }
    fn to_list(&self, py: Python<'_>) -> PyResult<Py<PyAny>> { Ok(PyList::new(py, self.values.as_slice())?.into_any().unbind()) }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> { Ok(format!("mango_list({})", self.to_list(py)?.bind(py).repr()?)) }
}

#[pymodule]
fn mangoo_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(normalize_path, module)?)?;
    module.add_function(wrap_pyfunction!(compile_selection, module)?)?;
    module.add_function(wrap_pyfunction!(serialize_json, module)?)?;
    module.add_class::<PyMangoList>()?;
    Ok(())
}
