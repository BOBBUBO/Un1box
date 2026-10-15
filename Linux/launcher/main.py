#!/usr/bin/env python3
"""LightTouch desktop library and prepared-device setup."""
import argparse
import fcntl
import os
from pathlib import Path
import sys
import time
import uuid

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from model import FORMAT, VERSION, Profiles, SetupError, import_command, load_profile, save_json, start_command, validate
from session import Session, clean_exit

PALETTE = {"paper": "#F3F6FA", "ink": "#1C304A", "muted": "#596C83", "blue": "#285BB5", "line": "#D9E2EF", "white": "#FFFFFF"}


class Launcher:
    def __init__(self, window, args):
        self.window, self.args = window, args
        self.store = Profiles(args.data_dir)
        self.session = Session()
        self.current = None
        self.closing = False
        self.job = ""
        self.pending_import = None
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
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(200, self.tick)

    def _build(self):
        self.window.title("LightTouch")
        self.window.geometry("1080x800")
        self.window.minsize(880, 700)
        self.window.configure(bg=PALETTE["paper"])
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("DejaVu Sans", 10), background=PALETTE["paper"], foreground=PALETTE["ink"])
        style.configure("TButton", padding=(12, 8))
        style.configure("TEntry", padding=7, fieldbackground=PALETTE["white"])
        style.configure("Title.TLabel", font=("DejaVu Sans", 24, "bold"))
        style.configure("Heading.TLabel", font=("DejaVu Sans", 16, "bold"))
        style.configure("Muted.TLabel", foreground=PALETTE["muted"])
        style.configure("Accent.TButton", background=PALETTE["blue"], foreground="white")
        style.map("Accent.TButton", background=[("active", "#204B96"), ("disabled", "#A4B7D4")])
        shell = ttk.Frame(self.window, padding=24)
        shell.pack(fill="both", expand=True)
        top = ttk.Frame(shell)
        top.pack(fill="x", pady=(0, 20))
        ttk.Label(top, text="LightTouch", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="iOS device library", style="Muted.TLabel").pack(side="left", padx=18, pady=(10, 0))
        panes = ttk.Panedwindow(shell, orient="horizontal")
        panes.pack(fill="both", expand=True)
        sidebar = ttk.Frame(panes, padding=(0, 0, 20, 0))
        panes.add(sidebar, weight=1)
        ttk.Label(sidebar, text="Your devices", style="Heading.TLabel").pack(anchor="w", pady=(0, 12))
        self.devices = tk.Listbox(sidebar, bg=PALETTE["white"], fg=PALETTE["ink"], selectbackground=PALETTE["blue"],
                                  selectforeground="white", relief="flat", highlightthickness=1,
                                  highlightbackground=PALETTE["line"], font=("DejaVu Sans", 11), width=23,
                                  exportselection=False, activestyle="none")
        self.devices.pack(fill="both", expand=True)
        self.devices.bind("<<ListboxSelect>>", self.select)
        self.widgets.append(self.devices)
        self.button(sidebar, "Add prepared device", self.new).pack(fill="x", pady=(12, 6))
        self.button(sidebar, "Import configuration…", self.import_profile).pack(fill="x")
        content = ttk.Frame(panes, padding=(20, 0, 0, 0))
        panes.add(content, weight=3)
        self.heading = ttk.Label(content, text="Prepared device", style="Heading.TLabel")
        self.heading.pack(anchor="w")
        ttk.Label(content, text="Use your verified firmware and matching guest tools.", style="Muted.TLabel").pack(anchor="w", pady=(6, 14))
        form = ttk.Frame(content)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.field(form, 0, "Device name", "name")
        self.field(form, 1, "Prepared base", "base", "folder")
        self.field(form, 2, "Writable state", "state", "folder")
        self.field(form, 3, "Guest tools", "guest_package", "file")
        self.field(form, 4, "Boot asset (n72)", "bootrom", "file")
        ttk.Label(content, text="n81 uses prepared kboot.bin; leave the separate boot asset empty.\nState stays separate from the read-only base. New state folders are created at boot.",
                  style="Muted.TLabel", justify="left", wraplength=600).pack(anchor="w", pady=(8, 12))
        tools = ttk.Frame(content)
        tools.pack(fill="x")
        self.button(tools, "Import base copy…", self.import_base).pack(side="left")
        self.button(tools, "Export configuration…", self.export_profile).pack(side="left", padx=8)
        choices = ttk.Frame(content)
        choices.pack(fill="x", pady=(14, 8))
        audio = ttk.Checkbutton(choices, text="Audio", variable=self.audio)
        audio.pack(side="left")
        recover = ttk.Checkbutton(choices, text="Recover interrupted state", variable=self.recover)
        recover.pack(side="left", padx=20)
        self.widgets += [audio, recover]
        actions = ttk.Frame(content)
        actions.pack(fill="x", pady=(8, 14))
        self.button(actions, "Save device", self.save).pack(side="left")
        self.start_button = self.button(actions, "Start iOS", self.start, "Accent.TButton")
        self.start_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(actions, text="Stop guest", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        ttk.Separator(content).pack(fill="x", pady=(2, 14))
        ttk.Label(content, textvariable=self.message, font=("DejaVu Sans", 11, "bold"), wraplength=600).pack(anchor="w")
        ttk.Label(content, textvariable=self.detail, style="Muted.TLabel", wraplength=600).pack(anchor="w", pady=(5, 10))
        ttk.Label(content, textvariable=self.stats, justify="left", font=("DejaVu Sans Mono", 10), wraplength=620).pack(anchor="w", pady=(0, 10))
        ttk.Label(content, text="Activity", style="Muted.TLabel").pack(anchor="w")
        self.log = tk.Text(content, height=5, bg=PALETTE["white"], fg=PALETTE["ink"], relief="flat", borderwidth=0,
                           font=("DejaVu Sans Mono", 9), wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, pady=(5, 0))
        ttk.Label(shell, text="QEMU TCG  ·  Guest window: H Home / P Power / Space Pause / R Rotate", style="Muted.TLabel").pack(anchor="w", pady=(16, 0))

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
            self.button(parent, "Browse…", lambda: self.browse(name, browse)).grid(row=row, column=2, padx=(8, 0), pady=5)

    def browse(self, name, kind):
        value = filedialog.askdirectory(parent=self.window, title="Choose " + name.replace("_", " "), mustexist=name != "state") if kind == "folder" else filedialog.askopenfilename(parent=self.window, title="Choose " + name.replace("_", " "))
        if value:
            self.vars[name].set(value)

    def _list(self):
        self.devices.delete(0, "end")
        for index, device in enumerate(self.store.devices):
            self.devices.insert("end", device["name"])
            if device["id"] == self.store.selected:
                self.devices.selection_set(index)
                self._load(device)

    def _load(self, device):
        self.current = device["id"]
        for name, value in self.vars.items():
            value.set(device.get(name, ""))
        self.audio.set(device.get("audio", True))
        self.recover.set(False)
        self.heading.configure(text=device["name"])

    def select(self, _event=None):
        if self.session.active or not self.devices.curselection():
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
        self.message.set("Choose a read-only base and its matching guest tools.")

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
        archive = messagebox.askyesnocancel("Import prepared base", "Import an archive?\n\nYes: prepared-base archive with a verified SHA-256.\nNo: extracted prepared-base folder.\n\nThis creates a new immutable copy and preserves the source.", parent=self.window)
        if archive is None:
            return
        source = filedialog.askopenfilename(parent=self.window, title="Choose prepared-base archive") if archive else filedialog.askdirectory(parent=self.window, title="Choose extracted prepared base")
        if not source:
            return
        digest = simpledialog.askstring("Archive SHA-256", "Paste the verified archive SHA-256:", parent=self.window) if archive else ""
        if archive and digest is None:
            return
        parent = filedialog.askdirectory(parent=self.window, title="Choose parent for the new immutable copy")
        if not parent:
            return
        destination = str(Path(parent) / ("lighttouch-base-" + uuid.uuid4().hex[:12]))
        try:
            command = import_command(self.args.frontend, source, destination, digest.strip())
            self.pending_import = destination
            self.launch(command, "import")
        except (SetupError, OSError, RuntimeError) as error:
            self.error(error)

    def launch(self, command, job):
        log = self.store.directory / "logs" / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".log")
        self.session.start(command, log)
        self.job = job
        self.was_active = True
        for widget in self.widgets:
            widget.configure(state="disabled")
        self.stop_button.configure(state="normal" if job == "guest" else "disabled")
        self.message.set("Checking prepared assets…" if job == "guest" else "Importing an immutable base copy…")
        self.detail.set("Log: " + str(log))

    def start(self):
        try:
            profile = self.profile()
            command = start_command(self.args.frontend, self.args.engine, profile, self.recover.get())
            self.store.save(profile)
            self.launch(command, "guest")
        except (SetupError, OSError, RuntimeError) as error:
            self.error(error)

    def stop(self):
        try:
            self.session.stop()
            self.stop_button.configure(state="disabled")
            self.message.set("Stopping guest… state is retained until shutdown is confirmed.")
        except (OSError, RuntimeError) as error:
            self.error(error)

    def close(self):
        if self.session.active:
            if self.job == "import":
                messagebox.showinfo("Import in progress", "Wait for the immutable import to finish. The source remains unchanged.", parent=self.window)
                return
            if messagebox.askyesno("Stop guest and close?", "LightTouch will request a safe shutdown and wait for its result. Your writable state will be retained.", parent=self.window):
                self.closing = True
                self.stop()
        else:
            self.window.destroy()

    def tick(self):
        lines = self.session.poll()
        if lines:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.insert("end", "\n".join(self.session.tail))
            self.log.see("end")
            self.log.configure(state="disabled")
        if self.session.active and self.job == "guest":
            state = self.session.status.get("state")
            if not self.session.stopping and state:
                self.message.set({"1": "Starting iOS…", "2": "Guest running", "3": "Guest paused", "4": "Stopping guest…", "5": "Guest failed — retaining state"}.get(state, "Guest session"))
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
        if self.was_active and not self.session.active:
            self.was_active = False
            for widget in self.widgets:
                widget.configure(state="normal")
            self.stop_button.configure(state="disabled")
            if self.job == "import":
                if self.session.returncode == 0:
                    self.vars["base"].set(self.pending_import)
                    self.message.set("Imported immutable base. Select matching guest tools, then Save device.")
                else:
                    self.message.set("Import failed. Source and existing devices were preserved; inspect the log.")
            else:
                clean = clean_exit(self.session.returncode, self.session.final)
                self.message.set("Guest stopped cleanly. Ready for a fresh session." if clean else "Shutdown unconfirmed. State was retained; inspect the log before recovery.")
                if self.closing:
                    self.window.destroy()
                    return
        self.window.after(1000, self.tick)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    bundle = Path(__file__).resolve().parents[2]
    parser.add_argument("--frontend", default=str(bundle / "bin" / "lighttouch-linux"))
    parser.add_argument("--engine", default=str(bundle / "lib" / "lighttouch" / "libqemu-arm.so"))
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
