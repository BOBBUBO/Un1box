"""Actual shared Tk widgets with a non-Linux synthetic host, no guest."""
import os
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


if __name__ == "__main__":
    unittest.main()
