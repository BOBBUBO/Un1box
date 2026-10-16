"""Linux command adapter; shared saved-profile format remains unchanged."""
from pathlib import Path
import os
import re
import sys

_shared = Path(__file__).resolve().parents[2] / "Desktop"
sys.path.insert(0, str(_shared if _shared.is_dir() else Path(__file__).resolve().parents[1]))
from lighttouch_desktop.profiles import *

def start_command(frontend, engine, profile, allow_dirty=False):
    validate(profile, allow_dirty=allow_dirty)
    regular(frontend, "LightTouch frontend")
    regular(engine, "shared engine")
    if not os.access(frontend, os.X_OK):
        raise SetupError("The LightTouch frontend is not executable.")
    command = [str(frontend), "--base", absolute(profile["base"]), "--state", absolute(profile["state"]),
               "--engine", str(Path(engine).absolute()), "--package", absolute(profile["guest_package"]), "--trace-io"]
    if profile.get("bootrom"):
        command += ["--rom", absolute(profile["bootrom"])]
    if not profile.get("audio", True):
        command += ["--no-audio"]
    if allow_dirty:
        command += ["--recover"]
    return command


def import_command(frontend, source, destination, expected_sha256):
    from lighttouch_desktop.import_cli import import_command as shared_import
    return shared_import("linux", frontend, source, destination, expected_sha256)
