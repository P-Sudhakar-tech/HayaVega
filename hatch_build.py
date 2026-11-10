"""Build the optional hayavega_native Rust extension as part of a normal wheel build.

This runs automatically for `pip install .` / `pip install -e .` from the
repository root -- no separate `maturin develop` step, and no risk of
building from the wrong manifest and overwriting the `hayavega` package (see
`src/hayavega/addons/signals/README.md` for the collision that step used to
risk). Building the extension requires a Rust toolchain (`cargo` on PATH);
when it is missing, this hook silently does nothing and the framework runs
on its pure-Python fallbacks, exactly as before -- Rust stays optional.

Set HAYAVEGA_SKIP_NATIVE_BUILD=1 to force a pure-Python build even when Rust
is available, e.g. so CI can exercise the fallback path deterministically.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_CARGO_MANIFEST = Path("crates") / "hayavega-python" / "Cargo.toml"


class NativeExtensionBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._staging_dir: Path | None = None

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel" or os.environ.get("HAYAVEGA_SKIP_NATIVE_BUILD"):
            return
        cargo = shutil.which("cargo")
        if cargo is None:
            return

        root = Path(self.root)
        try:
            subprocess.run(
                [cargo, "build", "--release", "--manifest-path", str(root / _CARGO_MANIFEST)],
                check=True,
                cwd=root,
            )
        except subprocess.CalledProcessError:
            self.app.display_warning("hayavega_native build failed; installing the pure-Python fallback instead.")
            return

        built_library = self._find_built_library(root)
        if built_library is None:
            self.app.display_warning("hayavega_native build produced no recognizable library; installing the pure-Python fallback instead.")
            return

        # Stage the copy outside the source tree: a copy left in the repo root
        # would shadow the installed package on sys.path for anyone who later
        # runs Python from that directory -- the same hazard this hook exists
        # to remove.
        destination_name = "hayavega_native.pyd" if platform.system() == "Windows" else "hayavega_native.so"
        self._staging_dir = Path(tempfile.mkdtemp(prefix="hayavega-native-"))
        destination = self._staging_dir / destination_name
        shutil.copy2(built_library, destination)

        build_data["artifacts"].append(f"/{destination_name}")
        build_data["force_include"][str(destination)] = destination_name
        build_data["pure_python"] = False
        build_data["infer_tag"] = True
        self.app.display_info(f"Bundled the hayavega_native extension ({destination_name}) into this build.")

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        if self._staging_dir is not None:
            shutil.rmtree(self._staging_dir, ignore_errors=True)
            self._staging_dir = None

    @staticmethod
    def _find_built_library(root: Path) -> Path | None:
        target_dir = root / "target" / "release"
        candidates = (
            target_dir / "libhayavega_native.so",
            target_dir / "libhayavega_native.dylib",
            target_dir / "hayavega_native.dll",
        )
        return next((candidate for candidate in candidates if candidate.is_file()), None)
