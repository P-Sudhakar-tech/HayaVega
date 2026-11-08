# Installation

## Requirements

- Python 3.10 or newer
- (Optional, recommended) [Rust](https://rustup.rs/) and `cargo` on `PATH`, for the native `mangoo_native` extension

## From PyPI

```bash
pip install mangoo
```

This is the normal way to install Mangoo. The published wheel is the
pure-Python build; see [Install from source](#install-from-source) below
if you want the optional Rust-native extension built locally.

## Install from source

To build the optional Rust-native extension, or to work on Mangoo itself,
install directly from a local checkout of the repository instead:

```bash
cd mangoo-framework
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -e .
```

If `cargo` is on `PATH`, a `hatchling` build hook builds and bundles
`mangoo_native` automatically &mdash; there's no separate manual step. This
powers a handful of optional, individually benchmarked accelerations:

- Signals' Rust-backed worker-pool dispatcher (a Python `ThreadPoolExecutor`
  fallback is used automatically if the extension isn't present)
- `mango_list`, a Rust-backed list type
- The [Rust runtime](../topics/rust-runtime.md) (`Settings.server.engine = 'rust'`)
  and its optional TLS support

None of these are required for Mangoo to work &mdash; every feature above has
a pure-Python fallback or is entirely opt-in.

!!! tip "Skip the Rust build"
    Set `MANGOO_SKIP_NATIVE_BUILD=1` before `pip install -e .` to force a
    pure-Python install even when `cargo` is available &mdash; useful for a
    faster install when you don't need the native extension's features.

## Verify the install

```bash
python -c "import mangoo; print('Mangoo is installed')"
python -c "import mangoo_native" 2>/dev/null && echo "Native extension: built" || echo "Native extension: not built (pure-Python fallback active)"
```

## Development dependencies

If you're contributing to Mangoo itself rather than building an app with it:

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy
```

See [Testing](../topics/testing.md) for how the test suite itself is
organized.

Next: [Quickstart](quickstart.md).
