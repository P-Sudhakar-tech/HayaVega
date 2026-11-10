"""Hayavega collection syntax with a Rust-native implementation when available."""

from __future__ import annotations

from typing import Any

try:
    from hayavega_native import MangoList as _NativeMangoList
except ImportError:
    _NativeMangoList = None


class _FallbackMangoList(list):
    """Compatibility implementation used until the optional Rust extension is built."""

    def __repr__(self) -> str:
        return f"mango_list({list(self)!r})"

    def to_list(self) -> list:
        return list(self)


MangoList = _NativeMangoList or _FallbackMangoList


def mango_list(items=()) -> Any:
    """Create a Rust-backed sequence, or a behavior-compatible fallback."""
    return MangoList(items)
