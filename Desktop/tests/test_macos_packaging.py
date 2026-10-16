"""Asset-free shared bundle staging and Mach-O path resolution contracts."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("macos_shared_build", SOURCE / "macOS/build-shared.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_relocated_bundle_imports_actual_shared_host_widgets_and_original_profile_format(self):
        contents = self.root / "unrelated space.app/Contents"
        build.stage_python(SOURCE, contents)
        resources = contents / "Resources"
        code = """from pathlib import Path
import launcher
from lighttouch_desktop.launcher import Launcher
from lighttouch_desktop.macos import MacHost
from lighttouch_desktop.profiles import FORMAT
assert launcher.Launcher is Launcher
assert launcher.MacHost is MacHost
assert FORMAT == 'lighttouch-linux-profile'
assert Path(launcher.__file__).resolve().parent == Path.cwd()
assert 'Linux/launcher' not in __import__('sys').path
"""
        env = dict(os.environ, PYTHONPATH=str(resources), PYTHONNOUSERSITE="1")
        subprocess.run([sys.executable, "-s", "-c", code], cwd=resources, env=env, check=True,
                       capture_output=True, text=True, timeout=5)
        self.assertFalse(list(contents.rglob("*.itpack")))
        self.assertFalse(list(contents.rglob("device.lock.json")))

    def test_runtime_rejects_escaping_links_and_relocates_internal_absolute_links(self):
        runtime = self.root / "runtime"
        (runtime / "bin").mkdir(parents=True)
        executable = runtime / "bin/python3-real"
        executable.write_text("synthetic runtime"); executable.chmod(0o700)
        (runtime / "bin/python3").symlink_to(executable)
        target = self.root / "copied-runtime"
        build.stage_runtime(runtime, target)
        self.assertFalse((target / "bin/python3").readlink().is_absolute())
        self.assertEqual((target / "bin/python3").resolve(), target / "bin/python3-real")
        (runtime / "outside").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "stay inside"):
            build.python_runtime(runtime)

    def test_executable_path_is_resolved_in_executable_context_and_ambiguity_rejected(self):
        runtime, vendor = self.root / "runtime", self.root / "vendor"
        original = self.root / "libraries/extension.so"
        original.parent.mkdir(); original.touch()
        hostbin = self.root / "app/bin"; hostbin.mkdir(parents=True)
        library = hostbin.parent / "lib"; library.mkdir()
        dependency = library / "dependency.dylib"; dependency.touch()
        install = "@executable_path/../lib/dependency.dylib"
        self.assertEqual(build.resolve_dependency(install, original, runtime, vendor, {hostbin}), dependency)
        other = self.root / "other/bin"; other.mkdir(parents=True)
        other.parent.joinpath("lib").mkdir()
        other.parent.joinpath("lib/dependency.dylib").touch()
        with self.assertRaisesRegex(ValueError, "unambiguous"):
            build.resolve_dependency(install, original, runtime, vendor, {hostbin, other})

    def test_real_rpath_load_command_paths_are_used(self):
        original = self.root / "objects/libtop.dylib"; original.parent.mkdir(); original.touch()
        dependency = self.root / "closure/libnested.dylib"; dependency.parent.mkdir(); dependency.touch()
        paths = build.load_rpaths("Load command 12\n cmd LC_RPATH\n cmdsize 64\n path @loader_path/../closure (offset 12)\n")
        actual = build.resolve_dependency("@rpath/libnested.dylib", original,
                                          self.root / "runtime", self.root / "vendor", set(), paths)
        self.assertEqual(actual, dependency)

    def test_build_plan_names_shared_host_and_fresh_native_helper_with_one_job(self):
        commands = build.compile_commands(self.root / "source ; literal", self.root / "fresh scratch")
        self.assertEqual([command[-1] for command in commands], ["LightTouchDesktopHost", "LightTouchDevice"])
        for command in commands:
            self.assertEqual(command[command.index("--jobs") + 1], "1")
            self.assertEqual(command[command.index("--package-path") + 1], str(self.root / "source ; literal"))
        with self.assertRaisesRegex(ValueError, "outside"):
            build.private_path(SOURCE / "output")


if __name__ == "__main__":
    unittest.main()
