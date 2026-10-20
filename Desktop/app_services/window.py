"""Desktop app installation UI; all network/device work stays off Tk's thread."""
import queue
import shutil
import threading
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from .bridge import ServiceJob
from .catalog import CatalogClient, Target, incompatibility, preferred
from .ipa import InstallError


class AppsWindow:
    def __init__(self, parent, bridge, scratch, guest_running, on_error=None):
        self.bridge, self.scratch, self.guest_running = bridge, Path(scratch), guest_running
        self.on_error = on_error
        self.window = tk.Toplevel(parent)
        self.window.title("LightTouch · Apps")
        self.window.geometry("900x640")
        self.window.minsize(640, 480)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.client = None
        self.target = None
        self.service = None
        self.work = None
        self.events = queue.Queue(maxsize=256)
        self.cancelled = threading.Event()
        self.entries = []
        # The version picker: the chosen app's copies, and the rechecked copy Download takes.
        self.app = None
        self.copies = []
        self.candidate = None
        self.lookups = 0
        self.pending_download = None
        self.widgets = []
        self.query = tk.StringVar()
        self.message = tk.StringVar(value="Reading the running guest's device and iOS version…")
        self.target_label = tk.StringVar(value="Guest not connected")
        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Apps", font=("DejaVu Sans", 20, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.target_label).pack(anchor="w", pady=(4, 12))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.search())
        self.button(row, "Search Legacy Store", self.search).pack(side="left", padx=(8, 0))
        self.widgets.append(entry)
        lists = ttk.Frame(frame)
        lists.pack(fill="both", expand=True, pady=(12, 8))
        lists.columnconfigure(0, weight=1, uniform="lists")
        lists.columnconfigure(2, weight=1, uniform="lists")
        lists.rowconfigure(1, weight=1)
        ttk.Label(lists, text="Results").grid(row=0, column=0, sticky="w")
        ttk.Label(lists, text="Versions that run on this device").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.list = self.listbox(lists, 0)
        self.list.bind("<<ListboxSelect>>", self.selected)
        self.versions = self.listbox(lists, 2)
        self.versions.bind("<<ListboxSelect>>", self.version_selected)
        self.details = tk.StringVar(value="Select an app, then the version to install.")
        ttk.Label(frame, textvariable=self.details, wraplength=700, justify="left").pack(anchor="w", pady=(0, 12))
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        self.button(actions, "Download and install", self.download).pack(side="left")
        self.button(actions, "Install local IPA…", self.local).pack(side="left", padx=8)
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel)
        self.cancel_button.pack(side="left")
        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill="x", pady=(16, 8))
        ttk.Label(frame, textvariable=self.message, wraplength=700, justify="left").pack(anchor="w")
        ttk.Label(frame, text="Apps install into this guest's writable state. Open installed apps from the iOS Home screen.",
                  wraplength=700).pack(anchor="w", pady=(12, 0))
        self.window.after(100, self.poll)
        try:
            self.run_service("info")
        except Exception as error:
            self.error(error)

    def error(self, error):
        self.message.set(str(error))
        if self.on_error:
            self.on_error(error)

    def catalog_client(self):
        # Trust-store discovery can fail too; run it inside the background job
        # so that the error reaches the UI and the application log.
        if self.client is None:
            self.client = CatalogClient()
        return self.client

    @staticmethod
    def listbox(parent, column):
        value = tk.Listbox(parent, exportselection=False, height=12)
        value.grid(row=1, column=column, sticky="nsew", padx=(12, 0) if column else 0)
        bar = ttk.Scrollbar(parent, orient="vertical", command=value.yview)
        bar.grid(row=1, column=column + 1, sticky="ns")
        value.configure(yscrollcommand=bar.set)
        return value

    def button(self, parent, text, command):
        value = ttk.Button(parent, text=text, command=command)
        self.widgets.append(value)
        return value

    @property
    def busy(self):
        return (self.service is not None and self.service.active) or (self.work is not None and self.work.is_alive())

    def admit(self):
        if self.busy:
            raise InstallError("Wait for the current app operation.")
        if not self.guest_running() or not self.bridge() or not self.bridge().alive:
            raise InstallError("Start iOS with guest app services before installing apps.")

    def run_service(self, action, ipa=None):
        self.admit()
        self.service = ServiceJob(self.bridge().command(action, self.scratch, ipa), timeout=30 if action == "info" else 900,
                                  owns_install=action == "install",
                                  env=getattr(self.bridge(), "service_environment", lambda: None)(),
                                  log_path=self.scratch.parent / "service-logs" / ("app-" + uuid.uuid4().hex + ".jsonl"))
        self.message.set("Connecting to guest…" if action == "info" else "Preparing app…")

    def background(self, action):
        self.admit()
        self.cancelled.clear()
        def run():
            try:
                value = action()
                self.events.put({"background": value})
            except Exception as error:
                self.events.put({"error": str(error)})
        self.work = threading.Thread(target=run, daemon=True)
        self.work.start()

    def search(self):
        try:
            if self.target is None:
                self.run_service("info")
                return
            query, target = self.query.get().strip(), self.target
            self.background(lambda: {"apps": self.catalog_client().search(query, target)})
            self.message.set("Searching Legacy Store…")
        except Exception as error:
            self.error(error)

    def lookup(self, action):
        """Version list and copy checks: no guest work, so they run beside other operations.

        Each new lookup supersedes the last; a superseded result is dropped.
        """
        self.lookups += 1
        generation = self.lookups
        def run():
            try:
                value = action()
            except Exception as error:
                value = {"problem": str(error)}
            self.events.put({"lookup": value, "generation": generation})
        threading.Thread(target=run, daemon=True).start()

    def selected(self, _event=None):
        selected = self.list.curselection()
        if not selected or self.target is None:
            return
        app, target = self.entries[selected[0]], self.target
        self.app, self.copies, self.candidate = app, [], None
        self.versions.delete(0, "end")
        reason = incompatibility(app)
        if reason:
            self.lookups += 1
            self.details.set("{}\n{}".format(app["name"], reason))
            return
        self.details.set("{}\nLoading versions…".format(app["name"]))
        self.lookup(lambda: {"copies": self.catalog_client().compatible_versions(app, target)})

    def version_selected(self, _event=None):
        selected = self.versions.curselection()
        if not selected or self.app is None or self.target is None:
            return
        app, copy, target = self.app, self.copies[selected[0]], self.target
        self.candidate = None
        self.details.set("{} {}\nChecking this copy…".format(app["name"], copy["version"]))
        self.lookup(lambda: {"candidate": self.catalog_client().choose(app, copy["ipa_id"], target), "copy": copy})

    def show_lookup(self, value):
        name = self.app["name"] if self.app else ""
        if "problem" in value:
            self.details.set("{}\n{}".format(name, value["problem"]))
            if self.on_error:
                self.on_error(value["problem"])
        if "copies" in value:
            self.copies = value["copies"]
            for copy in self.copies:
                twin = sum(c["version"] == copy["version"] for c in self.copies) > 1
                # Two archived copies of one version need their copy number to tell apart.
                self.versions.insert("end", " · ".join(filter(None, (
                    copy["version"], "{:.1f} MB".format(copy["size"] / 1e6) if copy["size"] else None,
                    "iOS {}+".format(copy["minimum_os"]) if copy["minimum_os"] else None,
                    "copy {}".format(copy["ipa_id"]) if twin else None))))
            if not self.copies:
                self.details.set("{}\nNo version of this app runs on this device.".format(name))
                return
            index = next((i for i, c in enumerate(self.copies) if c["ipa_id"] == preferred(self.app["ipa_id"])), 0)
            self.versions.selection_set(index)
            self.versions.see(index)
            self.version_selected()
        if "candidate" in value:
            self.candidate = value["candidate"]
            self.details.set("{} {}\nThis copy runs on this device; availability is checked again before download.".format(
                name, value["copy"]["version"]))

    def local(self):
        try:
            self.admit()
            path = filedialog.askopenfilename(parent=self.window, title="Install decrypted IPA",
                                              filetypes=[("iOS app archive", "*.ipa"), ("All files", "*")])
            if path:
                self.run_service("install", path)
        except Exception as error:
            self.error(error)

    def download(self):
        try:
            self.admit()
            app, target = self.candidate, self.target
            if app is None or target is None:
                raise InstallError("Select an app and a version that runs on this device first.")
            if not messagebox.askyesno("Download and install?", "Download {} {} and install it into the running guest?".format(
                    app["name"], app.get("version", "")), parent=self.window):
                return
            def progress(count, total):
                try:
                    self.events.put_nowait({"phase": "download", "percent": count * 100 // total if total else None})
                except queue.Full:
                    pass
            self.background(lambda: {"download": str(self.catalog_client().download(app, target, self.scratch,
                                                                          progress=progress, cancelled=self.cancelled.is_set))})
            self.message.set("Downloading app…")
        except Exception as error:
            self.error(error)

    def cancel(self):
        try:
            self.cancelled.set()
            if self.service:
                self.service.cancel()
            self.message.set("Cancellation requested; an install already submitted must finish.")
        except InstallError as error:
            self.error(error)

    def before_stop(self):
        if self.service and self.service.active and self.service.mutating:
            raise InstallError("The guest is installing an app. Wait for completion before Stop.")
        self.cancelled.set()
        if self.service:
            self.service.cancel(guest_ended=True)

    def close(self):
        if self.busy:
            self.cancel()
            self.message.set("Wait for this operation to finish before closing Apps.")
            return
        self.window.destroy()

    def cleanup_download(self):
        if self.pending_download:
            path = Path(self.pending_download)
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
            self.pending_download = None

    def poll(self):
        if not self.window.winfo_exists():
            return
        if self.service:
            while True:
                try:
                    self.handle(self.service.events.get_nowait())
                except queue.Empty:
                    break
            if not self.guest_running() and self.service.active:
                self.service.cancel(guest_ended=True)
            if not self.service.active:
                if self.service.successful():
                    if self.service.target:
                        self.target = Target(**self.service.target)
                        self.target_label.set("{} · iOS {} · {}".format(self.target.product_type, self.target.ios, self.target.arch))
                    result = self.service.result
                    self.message.set("Installed {}. Open it from the iOS Home screen.".format(result["bundle_id"]) if result.get("installed") else "Guest connected. Search Legacy Store or select a local IPA.")
                else:
                    self.error(self.service.error or "Guest service failed; no successful install was confirmed.")
                self.cleanup_download()
                self.service = None
        while True:
            try:
                self.handle(self.events.get_nowait())
            except queue.Empty:
                break
        state = "disabled" if self.busy else "normal"
        for widget in self.widgets:
            widget.configure(state=state)
        self.cancel_button.configure(state="normal" if self.busy else "disabled")
        self.window.after(200, self.poll)

    def handle(self, event):
        if "error" in event:
            self.error(event["error"])
        if "lookup" in event:
            if event["generation"] == self.lookups:
                self.show_lookup(event["lookup"])
            return
        if "phase" in event:
            self.message.set(event.get("status") or {"download": "Downloading app…", "upload": "Sending to guest…",
                                                    "install": "Installing…"}.get(event["phase"], event["phase"]))
            if isinstance(event.get("percent"), (int, float)):
                self.progress["value"] = event["percent"]
        result = event.get("background", {})
        if "apps" in result:
            self.entries = result["apps"]
            self.app, self.copies, self.candidate = None, [], None
            self.lookups += 1
            self.versions.delete(0, "end")
            self.list.delete(0, "end")
            for app in self.entries:
                reason = incompatibility(app)
                self.list.insert("end", app["name"] + (" · " + reason if reason else ""))
            self.message.set("{} results. Select an app, then the version to install.".format(len(self.entries)))
        if "download" in result:
            self.pending_download = result["download"]
            # The thread queued its terminal result just before returning. It may
            # still be alive for a moment; defer admission until it has retired.
            self.window.after(50, self.install_download)

    def install_download(self):
        if self.work and self.work.is_alive():
            self.window.after(50, self.install_download)
            return
        try:
            if self.cancelled.is_set():
                raise InstallError("Download cancelled; no install was submitted.")
            self.run_service("install", self.pending_download)
        except Exception as error:
            self.cleanup_download()
            self.error(error)
