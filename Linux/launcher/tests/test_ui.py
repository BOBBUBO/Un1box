"""Real Tk setup checks with synthetic files; never starts an iOS guest."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from test_preparation import archive


@unittest.skipUnless(os.environ.get('DISPLAY'), 'requires an isolated X11 display')
class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.window = main.tk.Tk()
        self.addCleanup(self.destroy_window)
        args = argparse.Namespace(data_dir=str(self.root / 'library'), frontend='/missing/frontend', engine='/missing/engine')
        self.ui = main.Launcher(self.window, args)

    def destroy_window(self):
        # Tests use several Tk roots in one interpreter; retire owned callbacks
        # before their Tcl commands disappear and another root pumps events.
        for callback in self.window.tk.splitlist(self.window.tk.call('after', 'info')):
            self.window.after_cancel(callback)
        self.window.destroy()

    def test_file_picker_accepts_readonly_files_and_autofills_companions(self):
        source = archive(self.root / 'n72ap-7E18.itbase')
        for name in ('armv6.itpack', 'bootrom.bin'):
            (self.root / name).write_bytes(b'synthetic')
            (self.root / name).chmod(0o444)
        source.chmod(0o444)
        with patch.object(main.filedialog, 'askopenfilename', return_value=str(source)) as picker:
            self.ui.choose_system()
        self.assertIn('*.itbase', str(picker.call_args))
        self.assertEqual(self.ui.vars['guest_package'].get(), str(self.root / 'armv6.itpack'))
        self.assertEqual(self.ui.vars['bootrom'].get(), str(self.root / 'bootrom.bin'))
        self.assertEqual(self.ui.start_button['text'], 'Set up device')
        self.assertFalse(self.ui.session.active)
        self.assertFalse(self.ui.vars['base'].get())

    def test_completed_import_can_be_selected_without_hash_or_copy(self):
        base = self.root / 'already installed'
        base.mkdir()
        with patch.object(main.filedialog, 'askdirectory', return_value=str(base)):
            self.ui.browse('base', 'folder')
        self.assertIsNone(self.ui.selected_source)
        self.assertIsNone(self.ui.preparation)
        self.assertEqual(self.ui.vars['base'].get(), str(base))
        self.assertEqual(self.ui.start_button['text'], 'Start iOS')

    def test_session_controls_remain_visible_in_short_window(self):
        self.window.geometry('950x540')
        self.window.update()
        top = self.window.winfo_rooty()
        bottom = top + self.window.winfo_height()
        for button in (self.ui.start_button, self.ui.stop_button, self.ui.cancel_button, self.ui.apps_button):
            self.assertTrue(button.winfo_ismapped())
            self.assertGreaterEqual(button.winfo_rooty(), top)
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), bottom)

        # The third picker must remain reachable when setup needs scrolling.
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        boot = next(child for child in descendants(self.window)
                    if isinstance(child, main.ttk.Button) and child['text'] == 'Choose boot file…')
        panel = boot.master
        while not isinstance(panel, main.ScrollPanel):
            panel = panel.master
        panel.canvas.yview_moveto(1)
        self.window.update()
        self.assertGreaterEqual(boot.winfo_rooty(), panel.canvas.winfo_rooty())
        self.assertLessEqual(boot.winfo_rooty() + boot.winfo_height(),
                             panel.canvas.winfo_rooty() + panel.canvas.winfo_height())

    def test_replacing_saved_system_preserves_old_device_and_uses_new_state(self):
        old = self.ui.profile()
        old['base'] = str(self.root / 'original system')
        self.ui.store.save(old)
        self.ui._list()
        source = archive(self.root / 'n72ap-new.itbase')
        with patch.object(main.filedialog, 'askopenfilename', return_value=str(source)):
            self.ui.choose_system()
        self.assertNotEqual(self.ui.current, old['id'])
        self.assertNotEqual(self.ui.vars['state'].get(), old['state'])
        self.assertIn(old, self.ui.store.devices)

    def test_n81_existing_base_clears_old_boot_file(self):
        base = self.root / 'n81 base'
        base.mkdir()
        (base / 'device.lock.json').write_text(json.dumps(dict(format=1, board='n81ap', build='8C148', boot_strategy='kboot')))
        base.chmod(0o500)
        self.addCleanup(lambda: base.chmod(0o700))
        self.ui.vars['bootrom'].set('/old/bootrom.bin')
        with patch.object(main.filedialog, 'askdirectory', return_value=str(base)):
            self.ui.browse('base', 'folder')
        self.assertEqual(self.ui.vars['bootrom'].get(), '')

    def test_background_setup_completion_is_saved_for_next_launch(self):
        # Synthetic process stands in for the native importer only to exercise
        # GUI process completion, automatic persistence and the Start transition.
        source = archive(self.root / 'n72ap-7E18.itbase')
        self.ui.selected_source = str(source)
        self.ui.vars['guest_package'].set(str(self.root / 'armv6.itpack'))
        self.ui.vars['bootrom'].set(str(self.root / 'bootrom.bin'))
        producer = self.root / 'synthetic-importer'
        producer.write_text('#!/usr/bin/python3\nimport pathlib,sys\np=pathlib.Path(sys.argv[sys.argv.index("--base")+1]);p.mkdir();print("import 100%",flush=True)\n')
        producer.chmod(0o700)
        self.ui.args.frontend = str(producer)
        self.ui.import_base()
        deadline = time.monotonic() + 5
        heartbeats = 0
        while (self.ui.preparation or self.ui.was_active) and time.monotonic() < deadline:
            self.ui.tick()
            self.window.update()
            heartbeats += 1
            time.sleep(.01)
        self.assertGreater(heartbeats, 0)
        self.assertFalse(self.ui.session.active)
        self.assertEqual(self.ui.start_button['text'], 'Start iOS')
        self.assertIsNone(self.ui.selected_source)
        saved = json.loads(self.ui.store.file.read_text())
        self.assertEqual(saved['devices'][0]['base'], self.ui.pending_import)
        self.assertFalse(Path(saved['devices'][0]['state']).exists())


if __name__ == '__main__':
    unittest.main()
