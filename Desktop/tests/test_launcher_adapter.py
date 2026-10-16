"""Actual shared Tk widgets with a non-Linux synthetic host, no guest."""
import os
import json
from pathlib import Path
import sys
import tempfile
import time
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighttouch_desktop.launcher import Launcher, tk
from lighttouch_desktop.library import display_title
from lighttouch_desktop.storage import StorageOps
from lighttouch_desktop.windows import WindowsHost


class ImportDirectoryTests(unittest.TestCase):
    def test_import_uses_host_private_directory_before_preparing_archive(self):
        ui = object.__new__(Launcher)
        ui.vars = {"guest_package": SimpleNamespace(get=lambda: "armv7.itpack")}
        ui.selected_source = "n81ap.itbase"
        parent = Path.home() / "LightTouch import regression"
        ui.install_parent = SimpleNamespace(get=lambda: str(parent))
        ui.store = SimpleNamespace(storage=Mock())
        ui.host, ui.message, ui.detail = Mock(), Mock(), Mock()
        ui.set_busy, ui.error, ui.progress = Mock(), Mock(), {}
        with patch.object(Path, "mkdir", side_effect=AssertionError("must use host storage")):
            ui.import_base()
        ui.store.storage.ensure_directory.assert_called_once_with(parent)
        ui.host.prepare.assert_called_once_with("n81ap.itbase")
        ui.host.prepare.return_value.start.assert_called_once_with()
        self.assertEqual(Path(ui.pending_import).parent, parent)
        ui.error.assert_not_called()

        ui.host.reset_mock()
        ui.store.storage.ensure_directory.side_effect = OSError("not owned by this user")
        ui.import_base()
        ui.error.assert_called_once()
        ui.host.prepare.assert_not_called()


@unittest.skipUnless(os.environ.get("DISPLAY"), "requires an isolated X11 display")
class AdapterTests(unittest.TestCase):
    def test_widgets_use_injected_library_session_and_profile_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = dict(id="a" * 32, name="Synthetic host", base="/prepared/system", state="/private/instance", guest_package="/package.itpack", bootrom="", audio=True)
            store = SimpleNamespace(directory=root, devices=[selected], selected=selected["id"],
                                    new_state=lambda identity: str(root / identity / "state"), persist=lambda: None)
            calls = []
            session = SimpleNamespace(stop=lambda: calls.append("stop"), active=False, stopping=False,
                                      poll=lambda: [],
                                      record_error=lambda component,error: calls.append((component,str(error))))
            adapter = SimpleNamespace(font_family="TkDefaultFont", open_profiles=lambda path: store,
                                      create_session=lambda log: session, inspect_base=lambda path: dict(board="n81ap", build="8C148"),
                                      display_title=display_title)
            window = tk.Tk()
            try:
                ui = Launcher(window, SimpleNamespace(data_dir=str(root)), adapter)
                window.update_idletasks()
                self.assertIs(ui.store, store)
                self.assertIs(ui.session, session)
                self.assertEqual(ui.current, selected["id"])
                self.assertEqual(ui.vars["guest_package"].get(), selected["guest_package"])
                self.assertIn("4.2.1", ui.devices.get(0))
                ui.stop()
                self.assertEqual(calls, ["stop"])
                self.assertEqual(str(ui.stop_button["state"]), "disabled")
                ui.preparation = SimpleNamespace(events=queue.Queue(), active=False,
                                                 error="The iOS system file changed during setup. Choose it again.")
                ui.preparation.events.put_nowait((4 * 1024**2, 235 * 1024**2))
                ui.setup_started = 0
                ui.tick()
                self.assertIn("changed during setup", ui.message.get())
                self.assertNotIn("Checking file", ui.detail.get())
                self.assertEqual(float(ui.progress["value"]), 0)
                # Successful setup must leave import mode before a guest starts.
                ui.preparation = None
                ui.job, ui.pending_import = "import", "/prepared/imported"
                session.returncode = 0
                store.save = Mock()
                ui.finished()
                self.assertEqual(ui.job, "")
                self.assertIsNone(ui.pending_import)
                self.assertIn("Setup complete", ui.message.get())
                self.assertEqual(str(ui.start_button["state"]), "normal")
                session.start = Mock()
                ui.launch(["synthetic-native-host"], "guest")
                self.assertEqual(float(ui.progress["value"]), 0)
                self.assertEqual(ui.message.get(), "Starting iOS…")
                # Native running status drives the shared UI and Apps readiness.
                session.active, session.status = True, {"state": "2"}
                ui.bridge = object()
                ui.last_metrics = time.monotonic() + 10
                ui.tick()
                self.assertEqual(ui.message.get(), "Guest running")
                self.assertEqual(str(ui.apps_button["state"]), "normal")
                # A missing bridge remains unavailable even with running status.
                ui.bridge = None
                ui.tick()
                self.assertEqual(str(ui.apps_button["state"]), "disabled")
            finally:
                for callback in window.tk.splitlist(window.tk.call("after", "info")):
                    window.after_cancel(callback)
                window.destroy()


@unittest.skipUnless(os.environ.get("DISPLAY"), "requires an isolated X11 display")
class RecoveryActionTests(unittest.TestCase):
    """Real widgets and host admission; session launch is isolated from guests."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "base"
        self.base.mkdir()
        (self.base / "device.lock.json").write_text(json.dumps(dict(
            format=1, board="n81ap", build="8C148", boot_strategy="kboot")))
        self.base.chmod(0o500)
        self.addCleanup(lambda: self.base.chmod(0o700))
        self.state = self.root / "saved-state"
        self.state.mkdir()
        self.saved = self.state / "guest-data"
        self.saved.write_bytes(b"keep my iOS changes")
        self.args = SimpleNamespace(data_dir=self.root / "library",
                                    frontend=self.root / "host.exe", worker=self.root / "worker.exe")
        self.package = self.root / "tools.itpack"
        for fixture in (self.args.frontend, self.args.worker, self.package):
            fixture.write_bytes(b"synthetic fixture; native admission remains authoritative")
        self.profile = dict(id="a" * 32, name="Saved device", base=str(self.base), state=str(self.state),
                            guest_package=str(self.package), bootrom="", audio=True)
        self.host = WindowsHost(storage=StorageOps())
        store = self.host.open_profiles(self.args.data_dir)
        store.save(self.profile)
        self.session = SimpleNamespace(active=False, stopping=False, start=Mock(), record_error=Mock(),
                                       poll=lambda: [], returncode=1, final={})
        def started(command, log, env=None):
            self.session.active = True
            self.session.log_path = log
        self.session.start.side_effect = started
        self.host.create_session = lambda log: self.session
        self.host.start_services = Mock(return_value=(None, None))
        self.host.guest_command = Mock(wraps=self.host.guest_command)
        self.window = tk.Tk()
        self.addCleanup(self.close_window)
        self.ui = Launcher(self.window, self.args, self.host)
        self.error_dialog = self.enterContext(patch("lighttouch_desktop.launcher.messagebox.showerror"))

    def close_window(self):
        for callback in self.window.tk.splitlist(self.window.tk.call("after", "info")):
            self.window.after_cancel(callback)
        self.window.destroy()

    def assert_retained(self, marker):
        self.assertEqual(marker.read_bytes(), b"interrupted")
        self.assertEqual(self.saved.read_bytes(), b"keep my iOS changes")

    def test_each_dirty_marker_offers_visible_explicit_one_shot_boot(self):
        for name in (".dirty", "shutdown-unconfirmed.json"):
            with self.subTest(marker=name):
                marker = self.state / name
                marker.write_bytes(b"interrupted")
                self.session.start.reset_mock()
                self.host.start_services.reset_mock()
                self.assertFalse(self.ui.recovery_button.winfo_manager())
                self.ui.start_button.invoke()
                self.window.geometry("950x540")
                self.window.update_idletasks()
                self.assertTrue(self.ui.recovery_button.winfo_ismapped())
                self.assertEqual(self.ui.recovery_button["text"], "Boot recovered state")
                self.assertIn("unexpectedly", self.ui.message.get())
                self.assertIn("saved data", self.ui.detail.get())
                self.assertLess(self.ui.recovery_button.winfo_rooty(), self.ui.start_button.winfo_rooty())
                self.assertLess(self.ui.start_button.winfo_rooty(), self.window.winfo_rooty() + self.window.winfo_height())
                self.session.start.assert_not_called()
                self.host.start_services.assert_not_called()
                self.error_dialog.assert_not_called()
                self.assert_retained(marker)
                self.ui.recovery_button.invoke()
                self.session.start.assert_called_once()
                command = self.session.start.call_args.args[0]
                self.assertEqual(command[command.index("--recover") + 1], "yes")
                self.assertFalse(self.ui.recovery_button.winfo_manager())
                self.assertNotIn("recover", json.loads(self.ui.store.file.read_text())["devices"][0])
                self.assert_retained(marker)
                self.session.active = False
                self.ui.finished()
                self.session.start.reset_mock()
                self.ui.start_button.invoke()
                self.session.start.assert_not_called()
                # Ordinary Start never inherits the previous recovery decision.
                self.assertFalse(self.host.guest_command.call_args_list[-2].args[2])
                self.ui.clear_recovery()
                marker.unlink()

    def test_recovery_is_not_offered_for_missing_assets_or_runtime(self):
        marker = self.state / "shutdown-unconfirmed.json"
        marker.write_bytes(b"interrupted")
        for asset in (self.package, self.args.worker, self.args.frontend):
            with self.subTest(asset=asset.name):
                content = asset.read_bytes()
                asset.unlink()
                self.ui.start_button.invoke()
                self.assertFalse(self.ui.recovery_button.winfo_manager())
                self.session.start.assert_not_called()
                self.assertIn("regular", self.ui.message.get())
                self.assert_retained(marker)
                asset.write_bytes(content)
        self.assertEqual(self.error_dialog.call_count, 3)

    def test_recovery_preserves_identity_and_readonly_admission(self):
        marker = self.state / ".dirty"
        marker.write_bytes(b"interrupted")
        pin = self.state / ".base-identity"
        pin.write_text("a" * 64)
        self.ui.start_button.invoke()
        self.assertIn("another base", self.ui.message.get())
        self.assertFalse(self.ui.recovery_button.winfo_manager())
        pin.unlink()
        self.base.chmod(0o700)
        self.ui.start_button.invoke()
        self.assertIn("writable", self.ui.message.get())
        self.assertFalse(self.ui.recovery_button.winfo_manager())
        self.session.start.assert_not_called()
        self.assert_retained(marker)

    def test_configuration_changes_and_selection_cancel_the_offer(self):
        marker = self.state / ".dirty"
        marker.write_bytes(b"interrupted")
        self.ui.start_button.invoke()
        self.ui.vars["name"].set("Changed settings")
        self.assertFalse(self.ui.recovery_button.winfo_manager())
        self.ui.recovery_button.invoke()
        self.session.start.assert_not_called()
        self.ui.start_button.invoke()
        self.ui._load(dict(self.profile, id="b" * 32, state=str(self.root / "other-state")))
        self.assertFalse(self.ui.recovery_button.winfo_manager())
        self.ui.recovery_button.invoke()
        self.session.start.assert_not_called()
        self.assert_retained(marker)

    def test_native_launch_failure_keeps_state_and_requires_a_new_decision(self):
        marker = self.state / "shutdown-unconfirmed.json"
        marker.write_bytes(b"interrupted")
        self.ui.start_button.invoke()
        self.session.start.side_effect = RuntimeError("Writable state is already in use")
        self.ui.recovery_button.invoke()
        self.assertIn("already in use", self.ui.message.get())
        self.assertFalse(self.ui.recovery_button.winfo_manager())
        self.ui.recovery_button.invoke()
        self.session.start.assert_called_once()
        self.error_dialog.assert_called_once()
        self.assert_retained(marker)


if __name__ == "__main__":
    unittest.main()
