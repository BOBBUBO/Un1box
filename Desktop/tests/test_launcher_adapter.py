"""Actual shared Tk widgets with a non-Linux synthetic host, no guest."""
import os
from pathlib import Path
import sys
import tempfile
import queue
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighttouch_desktop.launcher import Launcher, tk
from lighttouch_desktop.library import display_title


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
            finally:
                for callback in window.tk.splitlist(window.tk.call("after", "info")):
                    window.after_cancel(callback)
                window.destroy()


if __name__ == "__main__":
    unittest.main()
