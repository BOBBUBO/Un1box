"""macOS adapter for the maintained shared launcher and native session runner.

Saved JSON profiles retain the existing desktop format. DeviceInstance plist
creation/admission stays in the native runner; this module never writes or
migrates an installed Cocoa library.
"""
import os
from pathlib import Path
import plistlib
import re
import uuid

from .profiles import (FORMAT, VERSION, Profiles, SetupError, absolute, display_title,
                       disjoint, inspect_base, load_profile, regular, save_json, validate)
from .profiles import read_json
from .preparation import Preparation
from .session import ManagedSession, strict_clean_exit
from .macos_services import NativeServicesState, SESSION_ENV


def managed_record(profile):
    """Read-only mapping into this configuration's isolated native state root."""
    info = validate(profile, allow_dirty=True)
    identity = profile.get("id", "")
    if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{32}", identity):
        raise SetupError("Choose a locally saved device configuration with a valid identity.")
    root = Path(absolute(profile["state"]))
    native_id = str(uuid.UUID(hex=identity)).upper()
    directory = root / "Devices" / native_id
    for path in (root, root / "Devices", directory, directory / "work"):
        if path.is_symlink():
            raise SetupError("Native writable state cannot use symbolic-link containers.")
    record = directory / "device.plist"
    if record.exists() or record.is_symlink():
        regular(record, "native device record")
        if record.stat().st_size > 1024**2:
            raise SetupError("Native device record exceeds 1 MiB.")
        try:
            value = plistlib.loads(record.read_bytes())
            same = (type(value.get("format")) is int and value["format"] == 1 and value.get("id") == native_id and
                    value.get("board") == info["board"] and
                    value.get("firmware") == info["board"] + "-" + info["build"] and
                    value.get("storage", {}).get("key") == info["base_identity"][:16] and
                    value["base"].get("kind") == "prepared" and
                    Path(value["base"]["path"]).resolve() == Path(profile["base"]).resolve())
        except (ValueError, TypeError, KeyError, AttributeError, plistlib.InvalidFileException) as error:
            raise SetupError("Native device record is invalid. Its files were retained.") from error
        if not same:
            raise SetupError("This state belongs to another native device or system. Select its saved configuration.")
    elif root.exists() and any(p.name not in (".shared-session-lease",) for p in root.iterdir()):
        raise SetupError("New native state must be empty. Existing Linux or Cocoa state is never converted automatically.")
    return dict(info, native_id=native_id, record=str(record), native_state=str(root))


clean_exit = strict_clean_exit


class MacSession(ManagedSession):
    """Native owner lifecycle plus a client-only app-services receipt consumer.

    A consumer does not enable Apps: the native publisher, owned USBMux daemon
    and packaged mobile libraries still need implementation and qualification.
    """
    def __init__(self, application_log=None, **kwargs):
        super().__init__(application_log, label="LightTouch shared macOS launcher", **kwargs)
        self.services = NativeServicesState()
        self._service_error = None

    def start(self, command, log_path, env=None):
        if self.active:
            raise RuntimeError("Stop the current device before starting another session.")
        environment = dict(os.environ if env is None else env)
        environment.pop(SESSION_ENV, None)
        profile = None
        if len(command) > 1 and command[1] == "run" and command.count("--id") == 1:
            index = command.index("--id")
            if index + 1 < len(command) and re.fullmatch("[a-f0-9]{32}", command[index + 1]):
                profile = command[index + 1]
        launch = uuid.uuid4().hex if profile else None
        self.services.reset(launch, profile)
        self._service_error = None
        if launch:
            environment[SESSION_ENV] = launch
        try:
            super().start(command, log_path, environment)
        except BaseException:
            self.services.reset(None, None)
            raise

    def poll(self):
        lines = super().poll()
        for line in lines:
            accepted = self.services.observe(line, self.child.pid if self.child else None)
            if accepted:
                self._service_error = None
            elif line.startswith("SERVICES ") and self.services.error != self._service_error:
                self._service_error = self.services.error
                try:
                    self.record_error("native app services", self.services.error)
                except OSError:
                    # record_error already retained the diagnostic in memory.
                    # A failed optional log append cannot block guest polling.
                    pass
        if self.stopping or self.child is None or self.child.poll() is not None:
            self.services.invalidate()
        return lines

    def stop(self):
        self.services.invalidate()
        return super().stop()


class MacHost:
    font_family = "Helvetica Neue"
    open_profiles = staticmethod(Profiles)
    create_session = staticmethod(MacSession)
    inspect_base = staticmethod(inspect_base)
    display_title = staticmethod(display_title)
    prepare = staticmethod(Preparation)
    clean_exit = staticmethod(clean_exit)

    @staticmethod
    def load_profile(path):
        profile = load_profile(path)
        original = read_json(path)
        if "id" in original:
            profile["id"] = original["id"]
        managed_record(profile)
        return profile

    @staticmethod
    def validate(profile, allow_dirty=False):
        info = validate(profile, allow_dirty=allow_dirty)
        managed_record(profile)
        return info

    @staticmethod
    def export_profile(path, profile):
        save_json(path, dict(profile, format=FORMAT, version=VERSION))

    @staticmethod
    def _runner(args):
        regular(args.frontend, "shared macOS native host")
        if not os.access(args.frontend, os.X_OK):
            raise SetupError("The shared macOS native host is not executable.")
        return [str(args.frontend)]

    @classmethod
    def guest_command(cls, args, profile, recover):
        cls.validate(profile, allow_dirty=recover)
        for path, label in ((args.engine, "native engine"), (args.helper, "native device helper"),
                            (args.firmwarekit, "native firmware admission worker")):
            regular(path, label)
        command = cls._runner(args) + ["run", "--id", profile["id"], "--name", profile["name"],
                  "--base", absolute(profile["base"]), "--state", absolute(profile["state"]),
                  "--package", absolute(profile["guest_package"]), "--engine", str(Path(args.engine).absolute()),
                  "--helper", str(Path(args.helper).absolute()), "--firmwarekit", str(Path(args.firmwarekit).absolute())]
        if profile.get("bootrom"):
            command += ["--rom", absolute(profile["bootrom"])]
        if recover:
            command += ["--recover"]
        if not profile.get("audio", True):
            command += ["--no-audio"]
        return command

    @classmethod
    def import_command(cls, args, source, destination, sha256):
        regular(source, "prepared base archive")
        if not re.fullmatch("[a-fA-F0-9]{64}", sha256):
            raise SetupError("The prepared archive needs its calculated SHA-256.")
        destination = Path(destination).absolute()
        if destination.exists() or destination.is_symlink():
            raise SetupError("Import destination already exists. Choose a new folder.")
        disjoint(source, destination)
        return cls._runner(args) + ["import", "--source", absolute(source), "--destination", str(destination),
                                   "--sha256", sha256.lower()]

    @staticmethod
    def start_services(args, directory, identity):
        # Guest app-services integration is a separate unqualified native slice.
        return None, None

    @staticmethod
    def apps_window(*args, **kwargs):
        raise SetupError("Guest app services are not available in this shared macOS development build.")
