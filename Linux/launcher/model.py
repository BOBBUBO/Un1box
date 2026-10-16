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
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if destination.exists():
        raise SetupError("Import destination already exists. Choose a new folder.")
    disjoint(source, destination)
    if not source.exists() or source.is_symlink():
        raise SetupError("Choose a prepared-base archive or extracted folder.")
    command = [str(frontend), "--import", str(source), "--base", str(destination)]
    if source.is_file():
        if not re.fullmatch("[A-Fa-f0-9]{64}", expected_sha256):
            raise SetupError("Enter the verified archive's 64-character SHA-256.")
        command += ["--sha256", expected_sha256.lower()]
    elif not source.is_dir():
        raise SetupError("Choose a regular prepared-base archive or folder.")
    return command
