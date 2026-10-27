"""Default-off, local-profile experimental engine environment; no native cache claims."""
import os
from pathlib import Path
import re
import stat

from model import SetupError, disjoint

KEYS = ("LT_TCG_IR_CACHE_ENABLE", "LT_TCG_IR_CACHE")


def worker_environment(directory, profile, enabled=False, inherited=None):
    """Prepare only a fresh worker's environment. Never edit process-global env.

    Selection is deliberately absent from imported/exported profile settings.
    No cache records are imported, cleared, repaired, or shared between profiles.
    """
    env = dict(os.environ if inherited is None else inherited)
    for key in KEYS:
        env.pop(key, None)
    if enabled is False:
        return env
    if enabled is not True:
        raise SetupError("Experimental cache selection must be true or false.")
    identity = profile.get("id", "")
    if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{32}", identity):
        raise SetupError("Experimental cache requires a local device identity.")
    directory = Path(directory).absolute()
    root = directory / "experimental-tcg-ir-cache"
    for asset in (profile["base"], profile["state"], profile["guest_package"]):
        disjoint(root, asset)
    if profile.get("bootrom"):
        disjoint(root, profile["bootrom"])
    parent = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for name in ("experimental-tcg-ir-cache", identity):
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            try:
                info = os.fstat(child)
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise SetupError("Experimental cache requires an existing private 0700 folder; files were retained.")
            except Exception:
                os.close(child)
                raise
            os.close(parent)
            parent = child
    finally:
        os.close(parent)
    env[KEYS[0]] = "experimental-v1"
    env[KEYS[1]] = str(root / identity)
    return env
