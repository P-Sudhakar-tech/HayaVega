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
//! Real HTTP/1.1 keep-alive is implemented (a shared `BufReader` per
//! connection, `Connection: keep-alive` by default, an idle-connection read
//! timeout). Hardened against the well-known failure modes of a
//! hand-written HTTP server: request smuggling via `Transfer-Encoding`/
//! malformed `Content-Length`, response splitting via header injection,
//! Slowloris (a per-request wall-clock deadline, not just a per-syscall
//! one), and a single connection panicking permanently shrinking the
//! worker pool.
//!
//! Optional TLS termination (`rustls`, the `ring` crypto provider -- no
//! OpenSSL/system-library dependency) is supported: pass `tls_cert`/
//! `tls_key` (PEM file paths) to `serve_wsgi` and every connection is
//! served over HTTPS instead of plain HTTP. HTTP/2 remains explicitly out
//! of scope -- it is built around multiplexing many streams over one
//! connection, which does not fit this server's synchronous
//! thread-per-connection model without effectively becoming its own
//! scheduler, and its main real-world benefit (parallel loading of many
//! small browser-requested resources) does not apply to a backend API
//! server the way it does to a web page. A reverse proxy remains the
//! right place for HTTP/2, matching this project's own `DEPLOYMENT.md`
//! guidance for `gunicorn`/`waitress`.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use std::fs::File;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

/// Either a plain TCP connection or one wrapped in a TLS session. A single
/// enum (not a generic `handle_connection<T: Read + Write>`) because a
/// `rustls::StreamOwned` cannot be split into independent read/write
/// handles the way `TcpStream::try_clone()` splits a plain socket -- TLS
/// state is sequential and single-owner, so both directions of I/O for a
/// connection must go through the same object regardless of which variant
/// this is.
enum Conn {
    Plain(TcpStream),
    Tls(Box<rustls::StreamOwned<rustls::ServerConnection, TcpStream>>),
}

impl Conn {
    fn set_nodelay(&self, value: bool) -> io::Result<()> {
        match self {
            Conn::Plain(stream) => stream.set_nodelay(value),
            Conn::Tls(stream) => stream.sock.set_nodelay(value),
        }
    }

    fn set_read_timeout(&self, duration: Option<Duration>) -> io::Result<()> {
        match self {
            Conn::Plain(stream) => stream.set_read_timeout(duration),
            Conn::Tls(stream) => stream.sock.set_read_timeout(duration),
        }
    }

    fn peer_addr(&self) -> io::Result<std::net::SocketAddr> {
        match self {
            Conn::Plain(stream) => stream.peer_addr(),
            Conn::Tls(stream) => stream.sock.peer_addr(),
        }
    }
}

impl Read for Conn {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        match self {
            Conn::Plain(stream) => stream.read(buf),
            Conn::Tls(stream) => stream.read(buf),
        }
    }
}

impl Write for Conn {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        match self {
            Conn::Plain(stream) => stream.write(buf),
            Conn::Tls(stream) => stream.write(buf),
        }
    }

    fn flush(&mut self) -> io::Result<()> {
        match self {
            Conn::Plain(stream) => stream.flush(),
            Conn::Tls(stream) => stream.flush(),
        }
    }
}

/// Applied to every connection, including idle time between keep-alive
/// requests. Bounds how long a fixed-size worker thread can be pinned by a
/// client that opens a connection and then sends nothing further --
/// without this, enough such connections would exhaust the thread pool
/// (`workers`) and stall every other client, since this server has no
/// separate async I/O layer to hold idle connections without a thread.
const CONNECTION_READ_TIMEOUT: Duration = Duration::from_secs(30);

/// A Slowloris-style attacker sends real bytes just slowly enough that
/// each individual `read()` still completes inside `CONNECTION_READ_TIMEOUT`
/// -- that per-syscall timeout resets on every successful read and never
/// fires, so a client trickling one byte every 29 seconds would otherwise
/// pin a worker thread indefinitely. This is a separate, wall-clock
/// deadline for completing one whole request (headers plus body), checked
/// independently of the socket-level timeout above.
const REQUEST_READ_DEADLINE: Duration = Duration::from_secs(30);

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
fn read_request(reader: &mut BufReader<Conn>) -> Result<Option<ParsedRequest>, String> {
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
    // A request has now genuinely started (the idle-connection case above
    // already returned). Everything from here must complete within
    // REQUEST_READ_DEADLINE regardless of how many individual reads it
    // takes -- the per-syscall socket timeout alone does not bound this,
    // since it resets on every read that returns any data at all.
    let deadline = Instant::now() + REQUEST_READ_DEADLINE;
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
        if Instant::now() > deadline {
            return Err("request took too long to arrive (Slowloris-style deadline exceeded)".to_string());
        }
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

    // Request-smuggling defense (RFC 7230 SS3.3.3): a request naming both
    // `Content-Length` and `Transfer-Encoding` is exactly the ambiguity
    // smuggling attacks exploit -- reject it outright rather than guessing
    // which header a downstream proxy would honor.
    let has_content_length = headers.iter().any(|(name, _)| name.eq_ignore_ascii_case("content-length"));
    let transfer_encoding = headers.iter().find(|(name, _)| name.eq_ignore_ascii_case("transfer-encoding")).map(|(_, value)| value.to_ascii_lowercase());
    if has_content_length && transfer_encoding.is_some() {
        return Err("Content-Length and Transfer-Encoding must not both be present".to_string());
    }
    if let Some(encoding) = &transfer_encoding {
        // Chunked request bodies are not decoded by this version. Reading
        // zero bytes and continuing would leave the actual chunked body
        // sitting unread in the stream, which is misparsed as the *next*
        // request on a keep-alive connection -- the same desync class of
        // bug smuggling attacks rely on. Rejecting outright (and the
        // caller closing the connection, since the body's true end is
        // unknown) is the safe behavior; any other unrecognized
        // Transfer-Encoding is rejected for the same reason.
        return Err(format!("Transfer-Encoding {encoding:?} is not supported by this server"));
    }

    let content_length: u64 = match headers.iter().find(|(name, _)| name.eq_ignore_ascii_case("content-length")) {
        Some((_, value)) => value.trim().parse().map_err(|_| format!("malformed Content-Length: {value:?}"))?,
        None => 0,
    };
    if content_length > MAX_BODY_BYTES {
        return Err(format!("request body too large: {content_length} bytes"));
    }
    let mut body = vec![0u8; content_length as usize];
    if content_length > 0 {
        // Deliberately not a single `reader.read_exact(&mut body)`: a
        // client trickling a few bytes at a time would let each
        // underlying read succeed inside the per-syscall socket timeout
        // forever, the same Slowloris pattern the header-reading loop
        // above guards against -- so the body is read in a loop with its
        // own deadline check instead.
        let mut filled = 0usize;
        while filled < body.len() {
            if Instant::now() > deadline {
                return Err("request body took too long to arrive (Slowloris-style deadline exceeded)".to_string());
            }
            let read_now = reader.read(&mut body[filled..]).map_err(|e| e.to_string())?;
            if read_now == 0 {
                return Err("connection closed while reading the request body".to_string());
            }
            filled += read_now;
        }
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
        let _ = exc_info; // WSGI apps here never re-raise through start_response; Hayavega's own dispatcher already turns exceptions into a 500 Response before this is called.
        *self.result.lock().expect("StartResponse lock poisoned") = Some((status, response_headers));
        Ok(())
    }
}

fn build_environ<'py>(py: Python<'py>, request: &ParsedRequest, host: &str, port: u16, remote_addr: &str, is_https: bool) -> PyResult<Bound<'py, PyDict>> {
    let environ = PyDict::new(py);
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

    let io = py.import("io")?;
    let wsgi_input = io.call_method1("BytesIO", (PyBytes::new(py, &request.body),))?;
    environ.set_item("wsgi.input", wsgi_input)?;
    let sys = py.import("sys")?;
    environ.set_item("wsgi.errors", sys.getattr("stderr")?)?;
    environ.set_item("wsgi.version", (1, 0))?;
    environ.set_item("wsgi.url_scheme", if is_https { "https" } else { "http" })?;
    environ.set_item("wsgi.multithread", true)?;
    environ.set_item("wsgi.multiprocess", false)?;
    environ.set_item("wsgi.run_once", false)?;
    Ok(environ)
}

/// A header name or value containing a bare CR or LF could inject extra
/// header lines -- or, with a blank line, a forged second response --
/// into the stream (HTTP response splitting). A WSGI application's own
/// `start_response` call is exactly where an unsanitized value (e.g.
/// reflecting request input into a header) could introduce one, so this
/// server -- not just the application -- must be the backstop.
fn contains_crlf(value: &str) -> bool {
    value.contains('\r') || value.contains('\n')
}

fn write_response(stream: &mut Conn, status: &str, headers: &[(String, String)], body: &[u8], keep_alive: bool) -> std::io::Result<()> {
    if contains_crlf(status) || headers.iter().any(|(name, value)| contains_crlf(name) || contains_crlf(value)) {
        return write_response(stream, "500 Internal Server Error", &[("Content-Type".to_string(), "text/plain".to_string())], b"500 Internal Server Error: a response header contained a CR or LF byte", false);
    }
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
fn handle_connection(stream: Conn, application: &Py<PyAny>, host: &str, port: u16, is_https: bool) {
    // Without this, Nagle's algorithm on the response write can interact
    // with the peer's delayed-ACK timer and add tens of milliseconds of
    // pure protocol latency per request -- a classic, easy-to-miss pitfall
    // for a hand-written HTTP server, and one worth calling out explicitly
    // rather than leaving unexplained.
    let _ = stream.set_nodelay(true);
    let _ = stream.set_read_timeout(Some(CONNECTION_READ_TIMEOUT));
    let remote_addr = stream.peer_addr().map(|addr| addr.ip().to_string()).unwrap_or_default();
    // One `Conn` handles both directions: unlike a plain `TcpStream`,
    // `Conn::Tls` cannot be split into independent read/write handles via
    // `try_clone()` (TLS session state is sequential and single-owner), so
    // writes go through `reader.get_mut()` instead of a second handle to
    // the same connection.
    let mut reader = BufReader::new(stream);

    loop {
        let parsed = match read_request(&mut reader) {
            Ok(Some(request)) => request,
            Ok(None) => return, // client closed the connection
            Err(message) => {
                let _ = write_response(reader.get_mut(), "400 Bad Request", &[("Content-Type".to_string(), "text/plain".to_string())], format!("400 Bad Request: {message}").as_bytes(), false);
                return;
            }
        };
        let keep_alive = parsed.wants_keep_alive();

        let outcome = Python::attach(|py| -> PyResult<(String, Vec<(String, String)>, Vec<u8>)> {
            let environ = build_environ(py, &parsed, host, port, &remote_addr, is_https)?;
            let captured = Arc::new(Mutex::new(None));
            let start_response = Py::new(py, StartResponse { result: Arc::clone(&captured) })?;
            let result = application.call1(py, (environ, start_response))?;

            let mut body = Vec::new();
            {
                let bound = result.bind(py);
                let iterator = bound.try_iter()?;
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
            Ok((status, headers, body)) => write_response(reader.get_mut(), &status, &headers, &body, keep_alive),
            Err(error) => {
                Python::attach(|py| {
                    error.print(py);
                });
                write_response(reader.get_mut(), "500 Internal Server Error", &[("Content-Type".to_string(), "text/plain".to_string())], b"500 Internal Server Error", false)
            }
        };
        if write_result.is_err() || !keep_alive {
            return;
        }
    }
}

/// Loads a PEM certificate chain and private key from disk and builds a
/// `rustls::ServerConfig`. Errors are the caller's (`serve_wsgi`'s)
/// responsibility to surface clearly -- a misconfigured or missing
/// cert/key is a startup-time mistake, not a per-connection one, so it is
/// resolved once before the accept loop starts rather than on every
/// connection.
fn load_tls_config(cert_path: &str, key_path: &str) -> Result<rustls::ServerConfig, String> {
    let cert_file = File::open(cert_path).map_err(|e| format!("could not open TLS certificate {cert_path:?}: {e}"))?;
    let certs = rustls_pemfile::certs(&mut BufReader::new(cert_file)).collect::<Result<Vec<_>, _>>().map_err(|e| format!("could not parse TLS certificate {cert_path:?} as PEM: {e}"))?;
    if certs.is_empty() {
        return Err(format!("TLS certificate file {cert_path:?} contained no certificates"));
    }

    let key_file = File::open(key_path).map_err(|e| format!("could not open TLS private key {key_path:?}: {e}"))?;
    let key = rustls_pemfile::private_key(&mut BufReader::new(key_file)).map_err(|e| format!("could not parse TLS private key {key_path:?} as PEM: {e}"))?.ok_or_else(|| format!("TLS private key file {key_path:?} contained no private key"))?;

    rustls::ServerConfig::builder().with_no_client_auth().with_single_cert(certs, key).map_err(|e| format!("invalid TLS certificate/key pair ({cert_path:?}, {key_path:?}): {e}"))
}

#[pyfunction]
#[pyo3(signature = (application, host, port, workers=8, tls_cert=None, tls_key=None))]
pub fn serve_wsgi(py: Python<'_>, application: Py<PyAny>, host: String, port: u16, workers: usize, tls_cert: Option<String>, tls_key: Option<String>) -> PyResult<()> {
    let listener = TcpListener::bind((host.as_str(), port)).map_err(|e| PyValueError::new_err(format!("could not bind {host}:{port}: {e}")))?;
    let listener = Arc::new(listener);
    let application = Arc::new(application);
    let workers = workers.max(1);
    let shutdown = Arc::new(AtomicBool::new(false));

    let tls_config = match (tls_cert, tls_key) {
        (Some(cert_path), Some(key_path)) => Some(Arc::new(load_tls_config(&cert_path, &key_path).map_err(PyValueError::new_err)?)),
        (None, None) => None,
        _ => return Err(PyValueError::new_err("serve_wsgi: tls_cert and tls_key must both be provided, or neither")),
    };
    let is_https = tls_config.is_some();

    py.detach(|| {
        let mut handles = Vec::with_capacity(workers);
        for _ in 0..workers {
            let listener = Arc::clone(&listener);
            let application = Arc::clone(&application);
            let host = host.clone();
            let shutdown = Arc::clone(&shutdown);
            let tls_config = tls_config.clone();
            handles.push(thread::spawn(move || {
                while !shutdown.load(Ordering::Relaxed) {
                    match listener.accept() {
                        Ok((tcp_stream, _addr)) => {
                            let conn = match &tls_config {
                                Some(config) => match rustls::ServerConnection::new(Arc::clone(config)) {
                                    Ok(session) => Conn::Tls(Box::new(rustls::StreamOwned::new(session, tcp_stream))),
                                    Err(error) => {
                                        eprintln!("hayavega rust runtime: TLS session setup failed: {error}");
                                        continue;
                                    }
                                },
                                None => Conn::Plain(tcp_stream),
                            };
                            // A panic anywhere in handle_connection (an
                            // unexpected bug, not any of the error paths
                            // above, which all return normally) must not
                            // take the whole worker thread down with it --
                            // Rust threads don't restart, so that would
                            // permanently shrink the pool by one for the
                            // rest of the process's life. The GIL guard
                            // inside handle_connection's `Python::with_gil`
                            // call still releases the GIL correctly during
                            // unwinding (its Drop impl runs regardless), so
                            // catching here is safe, not just convenient.
                            let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| handle_connection(conn, &application, &host, port, is_https)));
                            if let Err(panic) = outcome {
                                let message = panic.downcast_ref::<&str>().copied().or_else(|| panic.downcast_ref::<String>().map(String::as_str)).unwrap_or("<non-string panic payload>");
                                eprintln!("hayavega rust runtime: a connection handler panicked and was recovered: {message}");
                            }
                        }
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
