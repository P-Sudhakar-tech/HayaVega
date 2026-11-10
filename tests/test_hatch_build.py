"""Tests for the repo-root hatch_build.py custom build hook.

hatch_build.py is not part of the installed `hayavega` package -- it only runs
during a wheel build -- so it is loaded directly from its file path rather
than imported as a module.
"""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_HATCH_BUILD_PATH = Path(__file__).resolve().parents[1] / "hatch_build.py"
_spec = importlib.util.spec_from_file_location("_hatch_build_under_test", _HATCH_BUILD_PATH)
_hatch_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hatch_build)
NativeExtensionBuildHook = _hatch_build.NativeExtensionBuildHook


class FakeApp:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def display_warning(self, message):
        self.warnings.append(message)

    def display_info(self, message):
        self.infos.append(message)


def make_hook(root, *, target_name="wheel", app=None):
    return NativeExtensionBuildHook(str(root), {}, None, None, "", target_name, app=app or FakeApp())


class NativeExtensionBuildHookTests(unittest.TestCase):
    def test_skips_for_non_wheel_targets(self):
        hook = make_hook(Path("."), target_name="sdist")
        with patch("shutil.which") as which:
            hook.initialize("0.1.0", {"artifacts": [], "force_include": {}})
        which.assert_not_called()

    def test_skips_when_env_var_forces_pure_python(self):
        hook = make_hook(Path("."))
        with patch.dict("os.environ", {"HAYAVEGA_SKIP_NATIVE_BUILD": "1"}), patch("shutil.which") as which:
            hook.initialize("0.1.0", {"artifacts": [], "force_include": {}})
        which.assert_not_called()

    def test_skips_silently_when_cargo_is_not_on_path(self):
        hook = make_hook(Path("."))
        with patch("shutil.which", return_value=None), patch("subprocess.run") as run:
            build_data = {"artifacts": [], "force_include": {}}
            hook.initialize("0.1.0", build_data)
        run.assert_not_called()
        self.assertNotIn("pure_python", build_data)

    def test_warns_and_falls_back_when_cargo_build_fails(self):
        app = FakeApp()
        hook = make_hook(Path("."), app=app)
        with patch("shutil.which", return_value="/usr/bin/cargo"), patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "cargo")):
            build_data = {"artifacts": [], "force_include": {}}
            hook.initialize("0.1.0", build_data)
        self.assertTrue(app.warnings)
        self.assertNotIn("pure_python", build_data)

    def test_warns_and_falls_back_when_no_library_is_produced(self):
        app = FakeApp()
        hook = make_hook(Path("."), app=app)
        with patch("shutil.which", return_value="/usr/bin/cargo"), patch("subprocess.run"), patch.object(NativeExtensionBuildHook, "_find_built_library", return_value=None):
            build_data = {"artifacts": [], "force_include": {}}
            hook.initialize("0.1.0", build_data)
        self.assertTrue(app.warnings)
        self.assertNotIn("pure_python", build_data)

    def test_bundles_extension_when_build_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_library = Path(tmp) / "libhayavega_native.so"
            fake_library.write_bytes(b"fake-shared-object")
            app = FakeApp()
            hook = make_hook(Path(tmp), app=app)
            with patch("shutil.which", return_value="/usr/bin/cargo"), patch("subprocess.run"), patch.object(NativeExtensionBuildHook, "_find_built_library", return_value=fake_library), patch("platform.system", return_value="Linux"):
                build_data = {"artifacts": [], "force_include": {}}
                hook.initialize("0.1.0", build_data)

        self.assertIs(build_data["pure_python"], False)
        self.assertTrue(build_data["infer_tag"])
        self.assertIn("/hayavega_native.so", build_data["artifacts"])
        self.assertEqual(list(build_data["force_include"].values()), ["hayavega_native.so"])
        (staged_source,) = build_data["force_include"].keys()
        self.assertTrue(Path(staged_source).is_file())
        self.assertTrue(app.infos)

    def test_finalize_removes_the_staging_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_library = Path(tmp) / "libhayavega_native.so"
            fake_library.write_bytes(b"fake-shared-object")
            hook = make_hook(Path(tmp))
            with patch("shutil.which", return_value="/usr/bin/cargo"), patch("subprocess.run"), patch.object(NativeExtensionBuildHook, "_find_built_library", return_value=fake_library), patch("platform.system", return_value="Linux"):
                build_data = {"artifacts": [], "force_include": {}}
                hook.initialize("0.1.0", build_data)
            (staged_source,) = build_data["force_include"].keys()
            self.assertTrue(Path(staged_source).is_file())
            hook.finalize("0.1.0", build_data, "unused-artifact-path")
        self.assertFalse(Path(staged_source).exists())

    def test_bundled_extension_is_staged_outside_the_project_root(self):
        """A copy left inside the project root would shadow the installed
        package on sys.path for anyone running Python from that directory."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_library = root / "target" / "release" / "libhayavega_native.so"
            fake_library.parent.mkdir(parents=True)
            fake_library.write_bytes(b"fake-shared-object")
            hook = make_hook(root)
            with patch("shutil.which", return_value="/usr/bin/cargo"), patch("subprocess.run"), patch("platform.system", return_value="Linux"):
                build_data = {"artifacts": [], "force_include": {}}
                hook.initialize("0.1.0", build_data)

        (staged_source,) = build_data["force_include"].keys()
        self.assertFalse(Path(staged_source).is_relative_to(root))


if __name__ == "__main__":
    unittest.main()
