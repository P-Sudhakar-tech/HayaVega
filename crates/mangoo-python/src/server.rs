//! A minimal, real WSGI-compatible HTTP/1.1 server implemented in Rust.
//!
//! Scope decision, disclosed rather than silently made: this is a
//! synchronous, fixed-thread-pool server (the same concurrency model
//! `waitress` already uses -- one worker thread handles one connection at a
//! time, blocking I/O), not a `tokio`/`hyper`-based async runtime. CPython's
//! GIL means only one thread ever executes Python bytecode at a time
//! regardless of how the I/O layer is built, so an async I/O multiplexer
//! would not let more Python view code run concurrently than this design
//! already does -- the only thing async I/O could improve here is
//! connection-count scaling far beyond what a thread-per-connection pool
//! needs for this framework's realistic deployment sizes. Rust's role is
//! the same one every other native component in this crate plays: own a
//! narrow, bounded piece of work (socket accept, HTTP/1.1 parsing, response
//! writing) around Python code that is unchanged and still owns all
//! request-handling logic via the standard WSGI contract.
//!
//! No keep-alive: every connection is closed after one response, matching
//! HTTP/1.0-style semantics even when speaking HTTP/1.1. This is a real,
//! disclosed limitation (see `mangoo.rust_runtime`'s module docstring) --
//! implementing pipelined keep-alive correctly needs more parsing/timeout
//! machinery than this first version adds, and every benchmark scenario
//! this project runs already treats connections as short-lived per `oha`'s
//! default behavior on this suite's target endpoints.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

/// Applied to every connection, including idle time between keep-alive
/// requests. Bounds how long a fixed-size worker thread can be pinned by a
/// client that opens a connection and then sends nothing further --
/// without this, enough such connections would exhaust the thread pool
/// (`workers`) and stall every other client, since this server has no
/// separate async I/O layer to hold idle connections without a thread.
const CONNECTION_READ_TIMEOUT: Duration = Duration::from_secs(30);

const MAX_HEADER_LINE: usize = 16 * 1024;
const MAX_HEADERS: usize = 200;
const MAX_BODY_BYTES: u64 = 10 * 1024 * 1024;

struct ParsedRequest {
    method: String,
    path: String,
    query: String,
    version: String,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

impl ParsedRequest {
    /// HTTP/1.1 defaults to keep-alive unless `Connection: close` is sent;
    /// HTTP/1.0 defaults to close unless `Connection: keep-alive` is sent.
    fn wants_keep_alive(&self) -> bool {
        let connection_header = self.headers.iter().find(|(name, _)| name.eq_ignore_ascii_case("connection")).map(|(_, value)| value.to_ascii_lowercase());
        match connection_header.as_deref() {
            Some("close") => false,
            Some("keep-alive") => true,
            _ => self.version.eq_ignore_ascii_case("HTTP/1.1"),
        }
    }
}

/// Reads exactly one request from `reader`, a `BufReader` that must live for
/// the whole connection (not recreated per request) -- a fresh `BufReader`
/// per call would silently discard any bytes it read ahead into its
/// internal buffer past the current request's boundary, corrupting the
/// next pipelined request on a keep-alive connection.
fn read_request(reader: &mut BufReader<TcpStream>) -> Result<Option<ParsedRequest>, String> {
    let mut request_line = String::new();
    let bytes_read = match reader.read_line(&mut request_line) {
        Ok(n) => n,
        // A read timeout waiting for the *next* request on an idle
        // keep-alive connection is a normal close, not a malformed
        // request -- treated the same as clean EOF, not an error.
        Err(e) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => return Ok(None),
        Err(e) => return Err(e.to_string()),
    };
    if bytes_read == 0 {
        return Ok(None); // client closed the connection
    }
    if request_line.len() > MAX_HEADER_LINE {
        return Err("request line too long".to_string());
    }
    let request_line = request_line.trim_end();
    let mut parts = request_line.splitn(3, ' ');
    let method = parts.next().ok_or("malformed request line")?.to_string();
    let target = parts.next().ok_or("malformed request line")?.to_string();
    let version = parts.next().ok_or("malformed request line")?.to_string();

    let (path, query) = match target.split_once('?') {
        Some((p, q)) => (p.to_string(), q.to_string()),
        None => (target, String::new()),
    };

    let mut headers = Vec::new();
    loop {
        if headers.len() > MAX_HEADERS {
            return Err("too many headers".to_string());
        }
        let mut line = String::new();
        let n = reader.read_line(&mut line).map_err(|e| e.to_string())?;
        if n == 0 {
            return Err("connection closed while reading headers".to_string());
        }
        if line.len() > MAX_HEADER_LINE {
            return Err("header line too long".to_string());
        }
        let line = line.trim_end_matches(['\r', '\n']);
        if line.is_empty() {
            break;
        }
        let (name, value) = line.split_once(':').ok_or("malformed header line")?;
        headers.push((name.trim().to_string(), value.trim().to_string()));
    }

    let content_length: u64 = headers
        .iter()
        .find(|(name, _)| name.eq_ignore_ascii_case("content-length"))
        .map(|(_, value)| value.parse().unwrap_or(0))
        .unwrap_or(0);
    if content_length > MAX_BODY_BYTES {
        return Err(format!("request body too large: {content_length} bytes"));
    }
    let mut body = vec![0u8; content_length as usize];
    if content_length > 0 {
        reader.read_exact(&mut body).map_err(|e| e.to_string())?;
    }

    Ok(Some(ParsedRequest { method, path, query, version, headers, body }))
}

fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                if let Ok(hex) = std::str::from_utf8(&bytes[i + 1..i + 3]) {
                    if let Ok(value) = u8::from_str_radix(hex, 16) {
                        out.push(value);
                        i += 3;
                        continue;
                    }
                }
                out.push(bytes[i]);
                i += 1;
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            other => {
                out.push(other);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Captures the (status, headers) an invoked WSGI application passed to
/// `start_response`, so Rust can read them back after the call returns.
#[pyclass]
struct StartResponse {
    result: Arc<Mutex<Option<(String, Vec<(String, String)>)>>>,
}

#[pymethods]
impl StartResponse {
    #[pyo3(signature = (status, response_headers, exc_info=None))]
    fn __call__(&self, status: String, response_headers: Vec<(String, String)>, exc_info: Option<Bound<'_, PyAny>>) -> PyResult<()> {
        let _ = exc_info; // WSGI apps here never re-raise through start_response; Mangoo's own dispatcher already turns exceptions into a 500 Response before this is called.
        *self.result.lock().expect("StartResponse lock poisoned") = Some((status, response_headers));
        Ok(())
    }
}

fn build_environ<'py>(py: Python<'py>, request: &ParsedRequest, host: &str, port: u16, remote_addr: &str) -> PyResult<Bound<'py, PyDict>> {
    let environ = PyDict::new_bound(py);
    environ.set_item("REQUEST_METHOD", &request.method)?;
    environ.set_item("SCRIPT_NAME", "")?;
    environ.set_item("PATH_INFO", percent_decode(&request.path))?;
    environ.set_item("QUERY_STRING", &request.query)?;
    environ.set_item("SERVER_NAME", host)?;
    environ.set_item("SERVER_PORT", port.to_string())?;
    environ.set_item("SERVER_PROTOCOL", "HTTP/1.1")?;
    environ.set_item("REMOTE_ADDR", remote_addr)?;

    let mut content_type: Option<&str> = None;
    let mut content_length: Option<&str> = None;
    for (name, value) in &request.headers {
        if name.eq_ignore_ascii_case("content-type") {
            content_type = Some(value.as_str());
            continue;
        }
        if name.eq_ignore_ascii_case("content-length") {
            content_length = Some(value.as_str());
            continue;
        }
        let key = format!("HTTP_{}", name.to_uppercase().replace('-', "_"));
        environ.set_item(key, value)?;
    }
    if let Some(value) = content_type {
        environ.set_item("CONTENT_TYPE", value)?;
    }
    if let Some(value) = content_length {
        environ.set_item("CONTENT_LENGTH", value)?;
    }

    let io = py.import_bound("io")?;
    let wsgi_input = io.call_method1("BytesIO", (PyBytes::new_bound(py, &request.body),))?;
    environ.set_item("wsgi.input", wsgi_input)?;
    let sys = py.import_bound("sys")?;
    environ.set_item("wsgi.errors", sys.getattr("stderr")?)?;
    environ.set_item("wsgi.version", (1, 0))?;
    environ.set_item("wsgi.url_scheme", "http")?;
    environ.set_item("wsgi.multithread", true)?;
    environ.set_item("wsgi.multiprocess", false)?;
    environ.set_item("wsgi.run_once", false)?;
    Ok(environ)
}

fn write_response(stream: &mut TcpStream, status: &str, headers: &[(String, String)], body: &[u8], keep_alive: bool) -> std::io::Result<()> {
    let mut out = Vec::with_capacity(body.len() + 256);
    out.extend_from_slice(b"HTTP/1.1 ");
    out.extend_from_slice(status.as_bytes());
    out.extend_from_slice(b"\r\n");
    let mut has_content_length = false;
    for (name, value) in headers {
        if name.eq_ignore_ascii_case("content-length") {
            has_content_length = true;
        }
        if name.eq_ignore_ascii_case("connection") {
            continue; // this function is the single source of truth for Connection
        }
        out.extend_from_slice(name.as_bytes());
        out.extend_from_slice(b": ");
        out.extend_from_slice(value.as_bytes());
        out.extend_from_slice(b"\r\n");
    }
    if !has_content_length {
        out.extend_from_slice(format!("Content-Length: {}\r\n", body.len()).as_bytes());
    }
    out.extend_from_slice(if keep_alive { b"Connection: keep-alive\r\n" } else { b"Connection: close\r\n" });
    out.extend_from_slice(b"\r\n");
    out.extend_from_slice(body);
    stream.write_all(&out)
}

/// Handles every request on one connection, keeping it open across
/// requests when the client asked to (HTTP/1.1 default, or an explicit
/// `Connection: keep-alive`) -- required for correctness under real load
/// on Windows specifically: without keep-alive, sustained concurrent
/// traffic opens and closes a new TCP connection per request fast enough
/// to exhaust Windows' ephemeral port range within seconds (the same
/// failure mode this project already documented for the Django benchmark
/// target in `benchmarks/README.md`), which shows up as a collapsing
/// success rate under load rather than just lower throughput.
fn handle_connection(mut stream: TcpStream, application: &Py<PyAny>, host: &str, port: u16) {
    // Without this, Nagle's algorithm on the response write can interact
    // with the peer's delayed-ACK timer and add tens of milliseconds of
    // pure protocol latency per request -- a classic, easy-to-miss pitfall
    // for a hand-written HTTP server, and one worth calling out explicitly
    // rather than leaving unexplained.
    let _ = stream.set_nodelay(true);
    let _ = stream.set_read_timeout(Some(CONNECTION_READ_TIMEOUT));
    let remote_addr = stream.peer_addr().map(|addr| addr.ip().to_string()).unwrap_or_default();
    let mut reader = BufReader::new(match stream.try_clone() {
        Ok(clone) => clone,
        Err(_) => return,
    });

    loop {
        let parsed = match read_request(&mut reader) {
            Ok(Some(request)) => request,
            Ok(None) => return, // client closed the connection
            Err(message) => {
                let _ = write_response(&mut stream, "400 Bad Request", &[("Content-Type".to_string(), "text/plain".to_string())], format!("400 Bad Request: {message}").as_bytes(), false);
                return;
            }
        };
        let keep_alive = parsed.wants_keep_alive();

        let outcome = Python::with_gil(|py| -> PyResult<(String, Vec<(String, String)>, Vec<u8>)> {
            let environ = build_environ(py, &parsed, host, port, &remote_addr)?;
            let captured = Arc::new(Mutex::new(None));
            let start_response = Py::new(py, StartResponse { result: Arc::clone(&captured) })?;
            let result = application.call1(py, (environ, start_response))?;

            let mut body = Vec::new();
            {
                let bound = result.bind(py);
                let iterator = bound.iter()?;
                for item in iterator {
                    let chunk: Vec<u8> = item?.extract::<Bound<'_, PyBytes>>()?.as_bytes().to_vec();
                    body.extend_from_slice(&chunk);
                }
            }
            if let Ok(close) = result.getattr(py, "close") {
                let _ = close.call0(py);
            }

            let (status, headers) = captured.lock().expect("StartResponse lock poisoned").take().ok_or_else(|| PyRuntimeError::new_err("WSGI application never called start_response()"))?;
            Ok((status, headers, body))
        });

        let write_result = match outcome {
            Ok((status, headers, body)) => write_response(&mut stream, &status, &headers, &body, keep_alive),
            Err(error) => {
                Python::with_gil(|py| {
                    error.print(py);
                });
                write_response(&mut stream, "500 Internal Server Error", &[("Content-Type".to_string(), "text/plain".to_string())], b"500 Internal Server Error", false)
            }
        };
        if write_result.is_err() || !keep_alive {
            return;
        }
    }
}

#[pyfunction]
#[pyo3(signature = (application, host, port, workers=8))]
pub fn serve_wsgi(py: Python<'_>, application: Py<PyAny>, host: String, port: u16, workers: usize) -> PyResult<()> {
    let listener = TcpListener::bind((host.as_str(), port)).map_err(|e| PyValueError::new_err(format!("could not bind {host}:{port}: {e}")))?;
    let listener = Arc::new(listener);
    let application = Arc::new(application);
    let workers = workers.max(1);
    let shutdown = Arc::new(AtomicBool::new(false));

    py.allow_threads(|| {
        let mut handles = Vec::with_capacity(workers);
        for _ in 0..workers {
            let listener = Arc::clone(&listener);
            let application = Arc::clone(&application);
            let host = host.clone();
            let shutdown = Arc::clone(&shutdown);
            handles.push(thread::spawn(move || {
                while !shutdown.load(Ordering::Relaxed) {
                    match listener.accept() {
                        Ok((stream, _addr)) => handle_connection(stream, &application, &host, port),
                        Err(_) => continue,
                    }
                }
            }));
        }
        for handle in handles {
            let _ = handle.join();
        }
    });
    Ok(())
}

/// Registers `serve_wsgi` on the given module -- kept as a plain function
/// (not folded into `lib.rs`'s `#[pymodule]` body directly) so this file's
/// server logic stays self-contained and independently testable.
pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(serve_wsgi, module)?)?;
    module.add_class::<StartResponse>()?;
    Ok(())
}
