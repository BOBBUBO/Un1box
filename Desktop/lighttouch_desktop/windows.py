"""Shared Windows host; UI and profile/session algorithms remain common.

The native headless transition helper owns full asset verification, existing
C# archive/boot algorithms and preparation before the SDL worker. This module
never translates metadata into QEMU arguments. Native target qualification,
services transport and a packaged Python/Tk runtime remain separate gates.
"""
from pathlib import Path
import os
import re

from .profiles import (FORMAT, VERSION, Profiles, SetupError, absolute, display_title,
                       disjoint, inspect_base, load_profile, regular, save_json, validate)
from .preparation import Preparation
from .session import ManagedSession, strict_clean_exit
from .windows_storage import WindowsStorage


class WindowsSession(ManagedSession):
    """Unavailable Windows services cannot inherit another profile's bridge.

    Environment variables are case-insensitive on Windows. Apply the same
    rule to explicit mappings before handing them to the shared process owner.
    A future qualified services launch must inject its admitted endpoint through
    the native owner, not preserve arbitrary inherited loopback configuration.
    """
    def start(self, command, log_path, env=None):
        environment = dict(os.environ if env is None else env)
        for key in list(environment):
            name = key.upper()
            if (name in ("IT_USB_TCP", "USBMUXD_SOCKET_ADDRESS", "USBMUXD_QEMU_ADDR") or
                    name.startswith("LTW_USBMUX_") or name.startswith("LTW_SESSION_")):
                environment.pop(key)
        super().start(command, log_path, environment)


class WindowsHost:
    font_family = "Segoe UI"
    clean_exit = staticmethod(strict_clean_exit)
    display_title = staticmethod(display_title)

    def __init__(self, storage=None):
        self.storage = storage or WindowsStorage()

    def open_profiles(self, directory):
        return Profiles(directory, storage=self.storage)

    def create_session(self, application_log):
        return WindowsSession(application_log, storage=self.storage, label="LightTouch shared Windows launcher")

    def inspect_base(self, path):
        return inspect_base(path, storage=self.storage)

    def prepare(self, source):
        return Preparation(source, storage=self.storage)

    def load_profile(self, path):
        return load_profile(path, storage=self.storage)

    def export_profile(self, path, profile):
        save_json(path, dict(profile, format=FORMAT, version=VERSION), storage=self.storage)

    def validate(self, profile, allow_dirty=False):
        result = validate(profile, allow_dirty=allow_dirty, storage=self.storage)
        dirty = Path(profile["state"])/"shutdown-unconfirmed.json"
        if (dirty.exists() or dirty.is_symlink()) and not allow_dirty:
            raise SetupError("This saved device has an unconfirmed shutdown. Select recovery to retain and cold-boot its state.")
        return result

    def _helper(self, args):
        regular(args.frontend, "shared Windows headless host", storage=self.storage)
        return [str(Path(args.frontend).absolute())]

    def guest_command(self, args, profile, recover):
        self.validate(profile, allow_dirty=recover)
        identity = profile.get("id", "")
        if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{32}", identity):
            raise SetupError("Select a saved device configuration with a valid identity.")
        regular(args.worker, "shared Windows SDL worker", storage=self.storage)
        command = self._helper(args) + ["run", "--id", identity, "--name", profile["name"],
                  "--base", absolute(profile["base"]), "--state", absolute(profile["state"]),
                  "--package", absolute(profile["guest_package"]), "--worker", str(Path(args.worker).absolute()),
                  "--recover", "yes" if recover else "no", "--audio", "yes" if profile.get("audio", True) else "no"]
        if profile.get("bootrom"):
            command += ["--rom", absolute(profile["bootrom"])]
        return command

    def import_command(self, args, source, destination, sha256):
        regular(source, "prepared base archive", storage=self.storage)
        if not re.fullmatch("[a-fA-F0-9]{64}", sha256):
            raise SetupError("The selected archive requires its calculated SHA-256.")
        destination = Path(destination).absolute()
        if destination.exists() or destination.is_symlink():
            raise SetupError("Import destination already exists. Choose a new folder.")
        disjoint(source, destination)
        return self._helper(args) + ["import", "--source", absolute(source),
                                    "--destination", str(destination), "--sha256", sha256.lower()]

    @staticmethod
    def start_services(args, directory, identity):
        # Shared Desktop/app_services is authoritative. Windows still needs
        # native guest-service transport; no copied catalog/IPA implementation.
        return None, None

    @staticmethod
    def apps_window(*args, **kwargs):
        raise SetupError("Guest app-service transport is not qualified in this shared Windows development build.")
