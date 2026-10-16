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
runtime_spec = importlib.util.spec_from_file_location("macos_python_runtime", SOURCE / "macOS/python-runtime.py")
runtime = importlib.util.module_from_spec(runtime_spec)
runtime_spec.loader.exec_module(runtime)


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

    def provider(self):
        prefix = self.root / "provider/python"
        for name in ("bin/python3.13", "Python", "lib/python3.13/encodings/__init__.py",
                     "lib/python3.13/tkinter/__init__.py", "lib/python3.13/os.py"):
            path = prefix / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("pinned public runtime input " + name)
            path.chmod(0o755 if name.startswith("bin/") else 0o644)
        extension = self.root / "provider/tk/_tkinter.cpython-313-darwin.so"
        extension.parent.mkdir(parents=True); extension.write_text("explicit Tk provider")
        tcl, tk = self.root / "provider/tcl9.1", self.root / "provider/tk9.1"
        tcl.mkdir(); tk.mkdir()
        (tcl / "init.tcl").write_text("tcl scripts"); (tk / "tk.tcl").write_text("tk scripts")
        return prefix, extension, tcl, tk

    def test_curated_runtime_ignores_external_site_packages_and_survives_provider_path_relocation(self):
        prefix, extension, tcl, tk = self.provider()
        private = self.root / "unrelated-private-packages"; private.mkdir()
        (private / "secret.py").write_text("must never enter runtime")
        (prefix / "lib/python3.13/site-packages").symlink_to(private, target_is_directory=True)
        plan = runtime.plan_runtime(prefix, "3.13", extension, tcl, tk)
        output = self.root / "curated-runtime"
        runtime.export_runtime(plan, output)
        self.assertEqual((output / "bin/python3").resolve(), output / "bin/python3.13")
        self.assertEqual((output / "lib/python3.13/lib-dynload" / extension.name).read_text(), "explicit Tk provider")
        self.assertFalse(any("secret" in p.name or "site-packages" in p.parts for p in output.rglob("*")))
        origins, environment = build.runtime_provenance(output)
        self.assertEqual(origins[output / "Python"], prefix / "Python")
        self.assertEqual(environment, {"TCL_LIBRARY": "lib/tcl9.1", "TK_LIBRARY": "lib/tk9.1"})
        moved = self.root / "relocated place/runtime"
        moved.parent.mkdir()
        build.stage_runtime(output, moved)
        self.assertEqual((moved / "bin/python3").resolve(), moved / "bin/python3.13")

    def test_export_rejects_changed_provider_before_creating_output(self):
        prefix, extension, tcl, tk = self.provider()
        plan = runtime.plan_runtime(prefix, "3.13", extension, tcl, tk)
        extension.write_text("changed extension")
        output = self.root / "rejected"
        with self.assertRaisesRegex(ValueError, "input changed"):
            runtime.export_runtime(plan, output)
        self.assertFalse(output.exists())

    def test_runtime_rejects_unmanifested_files_and_external_script_links(self):
        prefix, extension, tcl, tk = self.provider()
        plan = runtime.plan_runtime(prefix, "3.13", extension, tcl, tk)
        output = self.root / "runtime"
        runtime.export_runtime(plan, output)
        (output / "unreviewed.py").write_text("extra file")
        with self.assertRaisesRegex(ValueError, "unmanifested"):
            build.runtime_provenance(output)
        (tcl / "outside").symlink_to(prefix / "Python")
        with self.assertRaisesRegex(ValueError, "escapes"):
            runtime.plan_runtime(prefix, "3.13", extension, tcl, tk)

    def test_runtime_destinations_cannot_descend_through_a_file_or_link(self):
        prefix, extension, tcl, tk = self.provider()
        plan = runtime.plan_runtime(prefix, "3.13", extension, tcl, tk)
        plan["files"]["bin/python3/extra"] = dict(plan["files"]["Python"])
        with self.assertRaisesRegex(ValueError, "descends"):
            runtime.validate_manifest(plan)

    def test_minimum_macos_uses_all_actual_deployment_floors_not_sdk_versions(self):
        old = "Load command 1\n cmd LC_VERSION_MIN_MACOSX\n version 14.4\n sdk 26.0\n"
        sdl = "Load command 2\n cmd LC_BUILD_VERSION\n platform 1\n minos 26.0\n sdk 26.1\n"
        self.assertEqual(build.load_minimum_macos(old), "14.4")
        self.assertEqual(build.minimum_macos([build.load_minimum_macos(old), build.load_minimum_macos(sdl)]), "26.0")
        with self.assertRaisesRegex(ValueError, "native macOS"):
            build.load_minimum_macos(sdl.replace("platform 1", "platform 2"))
        with self.assertRaisesRegex(ValueError, "declare"):
            build.load_minimum_macos("Load command 1\n cmd LC_UUID\n")

    def test_swift_resources_follow_observed_helper_context_and_relocate(self):
        binaries = self.root / "scratch"; binaries.mkdir()
        bundle = binaries / "dependency.bundle"; bundle.mkdir()
        (bundle / "resource.txt").write_text("public dependency resource")
        contents = self.root / "Test.app/Contents"
        helpers = contents / "Helpers"; helpers.mkdir(parents=True)
        info = {"format": "lighttouch-native-runtime-info", "version": 1,
                "guest_started": False, "bundle_url": str(helpers)}
        origins = build.stage_swift_resources(binaries, contents, {"host": info, "helper": info})
        self.assertIn("Resources/SwiftPM/dependency.bundle/resource.txt", origins)
        self.assertEqual((helpers / "dependency.bundle/resource.txt").read_text(), "public dependency resource")
        moved = self.root / "Elsewhere.app"
        (contents.parent).rename(moved)
        self.assertEqual((moved / "Contents/Helpers/dependency.bundle/resource.txt").read_text(), "public dependency resource")

    def test_swift_resource_lookup_outside_contents_is_rejected(self):
        binaries = self.root / "scratch"; binaries.mkdir()
        (binaries / "dependency.bundle").mkdir()
        contents = self.root / "Test.app/Contents"; contents.mkdir(parents=True)
        info = {"format": "lighttouch-native-runtime-info", "version": 1,
                "guest_started": False, "bundle_url": str(contents.parent)}
        with self.assertRaisesRegex(ValueError, "inside app Contents"):
            build.stage_swift_resources(binaries, contents, {"host": info})


if __name__ == "__main__":
    unittest.main()
