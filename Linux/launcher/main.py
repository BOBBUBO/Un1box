#!/usr/bin/env python3
"""LightTouch desktop library and prepared-device setup."""
import argparse
import fcntl
import os
import queue
import re
import shutil
from pathlib import Path
import sys
import time
import uuid

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from model import FORMAT, VERSION, Profiles, SetupError, import_command, load_profile, save_json, start_command, validate
from session import Session, clean_exit
from preparation import Preparation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_services.bridge import Bridge
from app_services.ipa import InstallError
from app_services.window import AppsWindow

PALETTE = {"paper": "#F3F6FA", "ink": "#1C304A", "muted": "#596C83", "blue": "#285BB5", "line": "#D9E2EF", "white": "#FFFFFF"}


class Launcher:
    def __init__(self, window, args):
        self.window, self.args = window, args
        self.store = Profiles(args.data_dir)
        self.session = Session()
        self.bridge = None
        self.apps = None
        self.current = None
        self.closing = False
        self.job = ""
        self.pending_import = None
        self.preparation = None
        self.selected_source = None
        self.setup_started = None
        self.cancel_requested = False
        self.last_metrics = 0
        self.system_file = tk.StringVar()
        self.install_parent = tk.StringVar(value=str(self.store.directory / "bases"))
        self.selection_hint = tk.StringVar(value="Extract your guest ZIP, then choose the three files below in order.")
        self.was_active = False
        self.widgets = []
        self.vars = {name: tk.StringVar() for name in ("name", "base", "state", "guest_package", "bootrom")}
        self.audio = tk.BooleanVar(value=True)
        self.recover = tk.BooleanVar(value=False)
        self.message = tk.StringVar(value="Choose a device, or add a prepared base to get started.")
        self.detail = tk.StringVar(value="Native integrity checks run before every boot.")
        self.stats = tk.StringVar(value="Worker CPU and memory become available after Start.")
        self._build()
        self._list()
        if not self.current:
            self.new()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(200, self.tick)

    def _build(self):
        self.window.title("LightTouch")
        self.window.geometry("1040x810")
        self.window.minsize(950, 760)
        self.window.configure(bg=PALETTE["paper"])
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("DejaVu Sans", 10), background=PALETTE["paper"], foreground=PALETTE["ink"])
        style.configure("TButton", padding=(12, 8))
        style.configure("TEntry", padding=7, fieldbackground=PALETTE["white"])
        style.configure("Title.TLabel", font=("DejaVu Sans", 23, "bold"))
        style.configure("Heading.TLabel", font=("DejaVu Sans", 15, "bold"))
        style.configure("Muted.TLabel", foreground=PALETTE["muted"])
        style.configure("Accent.TButton", background=PALETTE["blue"], foreground="white")
        style.map("Accent.TButton", background=[("active", "#204B96"), ("disabled", "#A4B7D4")])
        shell = ttk.Frame(self.window, padding=24)
        shell.pack(fill="both", expand=True)
        top = ttk.Frame(shell)
        top.pack(fill="x", pady=(0, 22))
        ttk.Label(top, text="LightTouch", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="Run a classic iOS device", style="Muted.TLabel").pack(side="left", padx=18, pady=(8, 0))
        body = ttk.Frame(shell)
        body.pack(fill="both", expand=True)
        sidebar = ttk.Frame(body, width=190, padding=(0, 0, 22, 0))
        sidebar.pack(side="left", fill="y")
        ttk.Label(sidebar, text="Saved devices", style="Heading.TLabel").pack(anchor="w", pady=(0, 12))
        self.devices = tk.Listbox(sidebar, bg=PALETTE["white"], fg=PALETTE["ink"], selectbackground=PALETTE["blue"],
                                  selectforeground="white", relief="flat", highlightthickness=1,
                                  highlightbackground=PALETTE["line"], font=("DejaVu Sans", 10), width=21,
                                  exportselection=False, activestyle="none")
        self.devices.pack(fill="both", expand=True)
        self.devices.bind("<<ListboxSelect>>", self.select)
        self.widgets.append(self.devices)
        self.button(sidebar, "+ Add iOS device", self.new).pack(fill="x", pady=(12, 0))
        content = ttk.Frame(body)
        content.pack(side="left", fill="both", expand=True)
        self.heading = ttk.Label(content, text="Add iOS device", style="Heading.TLabel")
        self.heading.pack(anchor="w", pady=(0, 12))
        notebook = ttk.Notebook(content)
        notebook.pack(fill="both", expand=True)
        setup = ttk.Frame(notebook, padding=16)
        advanced = ttk.Frame(notebook, padding=16)
        diagnostics = ttk.Frame(notebook, padding=16)
        notebook.add(setup, text="  iOS files  ")
        notebook.add(advanced, text="  Advanced  ")
        notebook.add(diagnostics, text="  Session details  ")
        ttk.Label(setup, textvariable=self.selection_hint, wraplength=620, style="Muted.TLabel").pack(anchor="w", pady=(0, 12))
        for title, hint, variable, action, caption in (
            ("1   iOS system", "Choose n72ap-7E18.itbase for iOS 3.1.3.", self.system_file, self.choose_system, "Choose .itbase…"),
            ("2   Guest tools", "Choose armv6.itpack for iOS 3.1.3 (armv7.itpack for iPod touch 4).", self.vars["guest_package"], lambda: self.browse("guest_package", "file"), "Choose .itpack…"),
            ("3   Boot file", "Choose bootrom.bin for iOS 3.1.3. iPod touch 4 does not need this file.", self.vars["bootrom"], lambda: self.browse("bootrom", "file"), "Choose boot file…"),
        ):
            row = ttk.Frame(setup)
            row.pack(fill="x", pady=(0, 16))
            ttk.Label(row, text=title, font=("DejaVu Sans", 11, "bold")).pack(anchor="w")
            ttk.Label(row, text=hint, style="Muted.TLabel", wraplength=620).pack(anchor="w", pady=(3, 6))
            picker = ttk.Frame(row)
            picker.pack(fill="x")
            self.button(picker, caption, action).pack(side="right", padx=(8, 0))
            entry = ttk.Entry(picker, textvariable=variable, state="readonly")
            entry.pack(side="left", fill="x", expand=True)
            variable.trace_add("write", lambda *_, entry=entry: self.window.after_idle(entry.xview_moveto, 1))
        ttk.Label(setup, text="Your original files stay unchanged. Setup checks and unpacks the system once;\nyour iOS changes are saved separately. Checks run automatically.",
                  style="Muted.TLabel", justify="left", wraplength=620).pack(anchor="w", pady=(0, 10))
        form = ttk.Frame(advanced)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.field(form, 0, "Device name", "name")
        self.field(form, 1, "Installed system folder", "base", "folder")
        self.field(form, 2, "Saved iOS changes", "state", "folder")
        ttk.Label(form, text="Install new systems in").grid(row=3, column=0, sticky="w", padx=(0, 12), pady=5)
        location = ttk.Entry(form, textvariable=self.install_parent)
        location.grid(row=3, column=1, sticky="ew", pady=5)
        self.widgets.append(location)
        self.button(form, "Choose folder…", self.choose_storage).grid(row=3, column=2, padx=(8, 0), pady=5)
        ttk.Label(advanced, text="Already imported your system? Select its folder above (the folder containing device.lock.json). It will be used without copying.\n\nStorage is managed automatically. Change these paths only to reuse or relocate an existing device.",
                  style="Muted.TLabel", wraplength=600).pack(anchor="w", pady=12)
        recover = ttk.Checkbutton(advanced, text="Recover after an interrupted shutdown (keeps saved data)", variable=self.recover)
        recover.pack(anchor="w", pady=8)
        self.widgets.append(recover)
        config = ttk.Frame(advanced)
        config.pack(fill="x", pady=12)
        self.button(config, "Import settings…", self.import_profile).pack(side="left")
        self.button(config, "Export settings…", self.export_profile).pack(side="left", padx=8)
        self.button(advanced, "Save device settings", self.save).pack(anchor="w")
        ttk.Label(diagnostics, textvariable=self.stats, justify="left", wraplength=610).pack(anchor="w", pady=(0, 12))
        self.log = tk.Text(diagnostics, height=8, bg=PALETTE["white"], fg=PALETTE["ink"], relief="flat",
                           font=("DejaVu Sans Mono", 9), wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)
        ttk.Label(content, textvariable=self.message, font=("DejaVu Sans", 11, "bold"), wraplength=650).pack(anchor="w", pady=(16, 4))
        ttk.Label(content, textvariable=self.detail, style="Muted.TLabel", wraplength=650).pack(anchor="w", pady=(0, 8))
        self.progress = ttk.Progressbar(content, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 10))
        actions = ttk.Frame(content)
        actions.pack(fill="x")
        self.start_button = self.button(actions, "Start iOS", self.start, "Accent.TButton")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop iOS", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(actions, text="Cancel setup", command=self.cancel_setup, state="disabled")
        self.cancel_button.pack(side="left")
        self.apps_button = ttk.Button(actions, text="Install apps…", command=self.open_apps, state="disabled")
        self.apps_button.pack(side="right")
        audio = ttk.Checkbutton(actions, text="Sound", variable=self.audio)
        audio.pack(side="right", padx=8)
        self.widgets.append(audio)

    def set_busy(self, busy):
        for widget in self.widgets:
            widget.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy and self.job != "guest" else "disabled")

    def choose_system(self):
        source = filedialog.askopenfilename(parent=self.window, title="Step 1: choose n72ap-7E18.itbase (iOS 3.1.3)",
                                           filetypes=[("iOS system (.itbase)", "*.itbase"), ("All files", "*")])
        if not source:
            return
        if not self.current:
            self.new()
        self.selected_source = source
        self.system_file.set(source)
        self.vars["base"].set("")
        parent = Path(source).parent
        self.vars["guest_package"].set("")
        self.vars["bootrom"].set("")
        packages = list(parent.glob("*.itpack"))
        preferred = parent / ("armv7.itpack" if "n81" in Path(source).name.lower() else "armv6.itpack")
        if preferred.is_file():
            self.vars["guest_package"].set(str(preferred))
        elif len(packages) == 1:
            self.vars["guest_package"].set(str(packages[0]))
        if "n81" not in Path(source).name.lower() and (parent / "bootrom.bin").is_file():
            self.vars["bootrom"].set(str(parent / "bootrom.bin"))
        self.start_button.configure(text="Set up device")
        self.message.set("iOS system selected. Check the guest tools and boot file, then Set up device.")
        self.detail.set("Setup will calculate checksums automatically and keep your original files unchanged.")

    def choose_storage(self):
        value = filedialog.askdirectory(parent=self.window, title="Choose storage for new iOS systems", mustexist=False)
        if value:
            self.install_parent.set(value)

    def button(self, parent, title, action, style="TButton"):
        widget = ttk.Button(parent, text=title, command=action, style=style)
        self.widgets.append(widget)
        return widget

    def field(self, parent, row, label, name, browse=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        entry = ttk.Entry(parent, textvariable=self.vars[name])
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        self.widgets.append(entry)
        if browse:
            title = "Choose folder…" if browse == "folder" else "Choose file…"
            self.button(parent, title, lambda: self.browse(name, browse)).grid(row=row, column=2, padx=(8, 0), pady=5)

    def browse(self, name, kind):
        titles = {"base": "Choose the imported folder containing device.lock.json", "state": "Choose where to save iOS changes",
                  "guest_package": "Step 2: choose armv6.itpack (iOS 3.1.3)", "bootrom": "Step 3: choose bootrom.bin (iOS 3.1.3)"}
        types = [("Guest tools (.itpack)", "*.itpack"), ("All files", "*")] if name == "guest_package" else [("Boot file (.bin)", "*.bin"), ("All files", "*")]
        value = filedialog.askdirectory(parent=self.window, title=titles[name], mustexist=name != "state") if kind == "folder" else filedialog.askopenfilename(parent=self.window, title=titles[name], filetypes=types)
        if value:
            self.vars[name].set(value)
            if name == "base":
                self.selected_source = None
                self.system_file.set(value)
                self.start_button.configure(text="Start iOS")
                self.message.set("Installed system selected. No import or extra copy is needed.")

    def _list(self):
        self.devices.delete(0, "end")
        for index, device in enumerate(self.store.devices):
            self.devices.insert("end", device["name"])
            if device["id"] == self.store.selected:
                self.devices.selection_set(index)
                self._load(device)

    def _load(self, device):
        self.current = device["id"]
        self.selected_source = None
        self.system_file.set(device.get("base", ""))
        self.start_button.configure(text="Start iOS" if device.get("base") else "Choose iOS system…")
        for name, value in self.vars.items():
            value.set(device.get(name, ""))
        self.audio.set(device.get("audio", True))
        self.recover.set(False)
        self.heading.configure(text=device["name"] if device.get("base") else "Add iOS device")

    def select(self, _event=None):
        if self.session.active or self.preparation or not self.devices.curselection():
            return
        device = self.store.devices[self.devices.curselection()[0]]
        self._load(device)
        self.store.selected = device["id"]
        self.store.persist()
        self.message.set("Ready to check prepared assets.")

    def new(self):
        identity = uuid.uuid4().hex
        self._load({"id": identity, "name": "iPod touch", "state": self.store.new_state(identity), "audio": True})
        self.devices.selection_clear(0, "end")
        self.message.set("Choose the iOS system file in step 1.")
        self.detail.set("For iOS 3.1.3: n72ap-7E18.itbase, armv6.itpack, bootrom.bin.")

    def profile(self):
        if not self.current:
            self.current = uuid.uuid4().hex
        result = {"id": self.current, "audio": self.audio.get()}
        result.update({name: value.get().strip() for name, value in self.vars.items()})
        if not result["state"]:
            result["state"] = self.store.new_state(self.current)
            self.vars["state"].set(result["state"])
        return result

    def error(self, error):
        self.message.set(str(error))
        messagebox.showerror("LightTouch", str(error), parent=self.window)

    def save(self):
        try:
            profile = self.profile()
            info = validate(profile, allow_dirty=True)
            self.store.save(profile)
            self._list()
            self.message.set("Saved · " + info["board"] + " · " + info["build"])
        except (SetupError, OSError) as error:
            self.error(error)

    def import_profile(self):
        name = filedialog.askopenfilename(parent=self.window, title="Import LightTouch configuration", filetypes=[("LightTouch configuration", "*.json"), ("All files", "*")])
        if not name:
            return
        try:
            profile = load_profile(name)
            self.store.save(profile)
            self._list()
            self.message.set("Configuration imported. Assets will be verified before boot.")
        except (SetupError, OSError) as error:
            self.error(error)

    def export_profile(self):
        try:
            profile = self.profile()
            validate(profile, allow_dirty=True)
            name = filedialog.asksaveasfilename(parent=self.window, title="Export configuration", defaultextension=".json")
            if name:
                save_json(name, dict(profile, format=FORMAT, version=VERSION))
        except (SetupError, OSError) as error:
            self.error(error)

    def import_base(self):
        if not self.vars["guest_package"].get():
            self.error(SetupError("Step 2: choose armv6.itpack for iOS 3.1.3, or armv7.itpack for iPod touch 4."))
            return
        if "n72" in Path(self.selected_source).name.lower() and not self.vars["bootrom"].get():
            self.error(SetupError("Step 3: choose bootrom.bin for this iOS 3.1.3 system."))
            return
        try:
            parent = Path(self.install_parent.get()).expanduser().absolute()
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.pending_import = str(parent / ("system-" + uuid.uuid4().hex))
        except OSError as error:
            self.error(error)
            return
        self.preparation = Preparation(self.selected_source)
        self.setup_started = time.monotonic()
        self.cancel_requested = False
        self.job = "prepare"
        self.set_busy(True)
        self.progress["value"] = 0
        self.message.set("Checking the iOS system file…")
        self.detail.set("Calculating its checksum automatically. Your files stay unchanged.")
        self.preparation.start()

    def cancel_setup(self):
        self.cancel_requested = True
        self.cancel_button.configure(state="disabled")
        self.message.set("Cancelling setup…")
        if self.preparation:
            self.preparation.cancelled.set()
        elif self.session.active and self.job == "import":
            try:
                self.session.stop()
            except (OSError, RuntimeError) as error:
                self.error(error)

    def launch(self, command, job, env=None):
        log = self.store.directory / "logs" / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".log")
        self.session.start(command, log, env=env)
        self.job = job
        self.was_active = True
        self.set_busy(True)
        self.stop_button.configure(state="normal" if job == "guest" else "disabled")
        self.message.set("Checking iOS files before boot…" if job == "guest" else "Preparing the iOS system…")
        self.detail.set("The first setup expands the system and checks its contents. This can take several minutes." if job == "import" else "Your iOS display will open in a separate window.")

    def start(self):
        if self.selected_source:
            self.import_base()
            return
        if not self.vars["base"].get():
            self.choose_system()
            return
        try:
            if self.apps and self.apps.window.winfo_exists():
                if self.apps.busy:
                    raise InstallError("Wait for the Apps operation before starting a fresh guest.")
                self.apps.window.destroy()
                self.apps = None
            profile = self.profile()
            command = start_command(self.args.frontend, self.args.engine, profile, self.recover.get())
            self.store.save(profile)
            env = None
            paths = (self.args.usbmuxd, self.args.imobile_library, self.args.plist_library)
            if all(Path(path).is_file() for path in paths):
                self.bridge = Bridge(*paths, self.store.directory / "devices" / self.current / "pairing",
                                     self.store.directory / "service-logs")
                env = self.bridge.start()
            self.launch(command, "guest", env=env)
            if self.bridge is None:
                self.detail.set("Guest app services are unavailable in this build. Log: " + str(self.session.log_path))
        except (SetupError, InstallError, OSError, RuntimeError) as error:
            if self.bridge:
                self.bridge.close()
                self.bridge = None
            self.error(error)

    def open_apps(self):
        if self.apps and self.apps.window.winfo_exists():
            self.apps.window.lift()
            return
        if not self.session.active or self.job != "guest" or self.session.stopping or not self.bridge:
            self.error(InstallError("Start iOS with the bundled guest app services to use Apps."))
            return
        self.apps = AppsWindow(self.window, lambda: self.bridge, self.store.directory / "app-downloads",
                               lambda: self.session.active and self.job == "guest" and not self.session.stopping)

    def stop(self):
        try:
            if self.apps and self.apps.window.winfo_exists():
                self.apps.before_stop()
            self.session.stop()
            self.stop_button.configure(state="disabled")
            self.message.set("Stopping guest… state is retained until shutdown is confirmed.")
        except (InstallError, OSError, RuntimeError) as error:
            self.closing = False
            self.error(error)

    def close(self):
        if self.preparation or (self.session.active and self.job == "import"):
            self.closing = True
            self.cancel_setup()
            return
        if self.session.active:
            if messagebox.askyesno("Stop guest and close?", "LightTouch will request a safe shutdown and wait for its result. Your writable state will be retained.", parent=self.window):
                self.closing = True
                self.stop()
        else:
            if self.apps and self.apps.busy:
                self.error(InstallError("Wait for the Apps operation to finish before closing."))
                return
            self.window.destroy()

    def tick(self):
        if self.preparation:
            prep = self.preparation
            while True:
                try:
                    done, total = prep.events.get_nowait()
                    percent = 100 * done / max(1, total)
                    self.progress["value"] = percent
                    self.detail.set("Checking file: %.0f%% · %.0f MiB of %.0f MiB · %ds elapsed" % (percent, done / 1024**2, total / 1024**2, time.monotonic() - self.setup_started))
                except queue.Empty:
                    break
            if not prep.active:
                self.preparation = None
                if prep.error or self.cancel_requested:
                    self.set_busy(False)
                    self.cancel_button.configure(state="disabled")
                    self.message.set(prep.error or "Setup cancelled. Your original files are unchanged.")
                    self.progress["value"] = 0
                else:
                    try:
                        needed = prep.result["expanded_bytes"]
                        if shutil.disk_usage(Path(self.pending_import).parent).free < needed + 256 * 1024**2:
                            raise SetupError("Not enough free space for setup. The expanded iOS system needs up to %.1f GiB. Choose another storage location in Advanced." % (needed / 1024**3))
                        command = import_command(self.args.frontend, self.selected_source, self.pending_import, prep.result["sha256"])
                        self.progress["value"] = 0
                        self.launch(command, "import")
                    except (SetupError, OSError, RuntimeError) as error:
                        self.set_busy(False)
                        self.cancel_button.configure(state="disabled")
                        self.error(error)
        lines = self.session.poll()
        if self.session.active and self.job == "import":
            for line in lines:
                match = re.fullmatch(r"import (\d+)%", line)
                if match:
                    percent = min(100, int(match[1]))
                    self.progress["value"] = percent
                    self.message.set("Unpacking iOS system: %d%%" % percent if percent < 100 else "Checking the unpacked system…")
            elapsed = int(time.monotonic() - self.setup_started)
            self.detail.set("%dm %02ds elapsed · The system can expand to many GiB. Original files stay unchanged." % divmod(elapsed, 60))
            if self.cancel_requested:
                self.message.set("Cancelling setup and removing the incomplete import…")
        if lines:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.insert("end", "\n".join(self.session.tail))
            self.log.see("end")
            self.log.configure(state="disabled")
        if self.session.active and self.job == "guest":
            state = self.session.status.get("state")
            self.apps_button.configure(state="normal" if state == "2" and self.bridge and not self.session.stopping else "disabled")
            if not self.session.stopping and state:
                self.message.set({"1": "Starting iOS…", "2": "Guest running", "3": "Guest paused", "4": "Stopping guest…", "5": "Guest failed — retaining state"}.get(state, "Guest session"))
            if time.monotonic() - self.last_metrics >= 1:
                self.last_metrics = time.monotonic()
                self.update_metrics()
        if self.was_active and not self.session.active:
            # The process/reader may finish just after the earlier poll. Reap it
            # and drain its terminal receipt before interpreting the result.
            self.session.poll()
            self.finished()
            if self.closing and not (self.apps and self.apps.busy):
                self.window.destroy()
                return
        if self.closing and not self.preparation and not self.session.active and not (self.apps and self.apps.busy):
            self.window.destroy()
            return
        self.window.after(200, self.tick)

    def update_metrics(self):
        metrics = self.session.metrics()
        host, worker = metrics["host"], metrics["worker"]
        text = "Worker CPU/RAM: waiting for native worker identity"
        if worker:
            cpu = "measuring" if worker["cpu_percent"] is None else "%.0f%%" % worker["cpu_percent"]
            memory = "unavailable" if worker["pss_bytes"] is None else "%.0f MiB PSS" % (worker["pss_bytes"] / 1024**2)
            text = "Emulator worker: %s CPU · %.1fs CPU · %s" % (cpu, worker["cpu_seconds"], memory)
        if "MemTotal" in host and "MemAvailable" in host:
            text += "\nHost RAM: %.0f / %.0f MiB used · CPU 100%% = one core" % ((host["MemTotal"] - host["MemAvailable"]) / 1024**2, host["MemTotal"] / 1024**2)
        if host.get("cpu_percent") is not None:
            text += "\nHost CPU: %.0f%% of all cores" % host["cpu_percent"]
        self.stats.set(text + "\nGuest OS CPU/RAM: unavailable")

    def finished(self):
        self.was_active = False
        if self.apps and self.apps.service:
            self.apps.service.cancel(guest_ended=True)
        if self.bridge:
            self.bridge.close()
            self.bridge = None
        self.set_busy(False)
        self.cancel_button.configure(state="disabled")
        self.apps_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        if self.job == "import":
            if self.session.returncode == 0:
                self.vars["base"].set(self.pending_import)
                self.selected_source = None
                self.system_file.set(self.pending_import)
                self.progress["value"] = 100
                self.start_button.configure(text="Start iOS")
                self.message.set("Setup complete. Click Start iOS.")
                self.detail.set("This system is installed. Future sessions reuse it without importing again.")
                try:
                    self.store.save(self.profile())
                    self._list()
                except (SetupError, OSError) as error:
                    self.error(error)
            else:
                self.progress["value"] = 0
                self.message.set("Setup cancelled. Your original files are unchanged." if self.cancel_requested else "Setup failed. See Session details for the error; your original files are unchanged.")
        else:
            clean = clean_exit(self.session.returncode, self.session.final)
            self.message.set("Guest stopped cleanly. Ready for a fresh session." if clean else "Shutdown unconfirmed. State was retained; inspect the log before recovery.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    bundle = Path(__file__).resolve().parents[2]
    parser.add_argument("--frontend", default=str(bundle / "bin" / "lighttouch-linux"))
    parser.add_argument("--engine", default=str(bundle / "lib" / "lighttouch" / "libqemu-arm.so"))
    parser.add_argument("--usbmuxd", default=str(bundle / "bin" / "usbmuxd-qemu"))
    parser.add_argument("--imobile-library", default=str(bundle / "lib" / "lighttouch" / "libimobiledevice-1.0.so.6"))
    parser.add_argument("--plist-library", default=str(bundle / "lib" / "lighttouch" / "libplist-2.0.so.4"))
    parser.add_argument("--data-dir", default=str(Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "lighttouch"))
    args = parser.parse_args()
    args.data_dir = str(Path(args.data_dir).expanduser().absolute())
    Path(args.data_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = open(Path(args.data_dir) / ".launcher-lease", "a")
    window = tk.Tk()
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        messagebox.showerror("LightTouch already open", "Use the existing LightTouch launcher for this library.", parent=window)
        window.destroy()
        return 1
    try:
        Launcher(window, args)
        window.mainloop()
    except (SetupError, OSError) as error:
        messagebox.showerror("LightTouch", str(error), parent=window)
        window.destroy()
        return 1
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
