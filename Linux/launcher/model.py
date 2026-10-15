"""Private launcher profiles. Native admission remains authoritative for guest assets."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

FORMAT = "lighttouch-linux-profile"
VERSION = 1
MAX_CONFIG_BYTES = 1024 * 1024


class SetupError(ValueError):
    pass


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SetupError("Choose a regular configuration file, not a symbolic link.")
    with path.open("rb") as stream:
        data = stream.read(MAX_CONFIG_BYTES + 1)
    if len(data) > MAX_CONFIG_BYTES:
        raise SetupError("Configuration is larger than 1 MiB.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SetupError("Duplicate configuration field: " + key)
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SetupError("Configuration must contain valid UTF-8 JSON.") from error
    if not isinstance(value, dict):
        raise SetupError("Configuration must be a JSON object.")
    return value


def absolute(value, relative_to=None):
    if not isinstance(value, str) or not value or "\0" in value:
        raise SetupError("Choose a valid file or folder path.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        if relative_to is None:
            raise SetupError("Choose an absolute path.")
        path = Path(relative_to) / path
    return str(path.absolute())


def disjoint(base, state):
    base, state = Path(base).resolve(), Path(state).resolve()
    if base == state or base in state.parents or state in base.parents:
        raise SetupError("Writable state must be separate from the prepared base.")


def inspect_base(base):
    """Cheap setup metadata only. Does not replace native complete integrity admission."""
    path = Path(base)
    if path.is_symlink() or not path.is_dir():
        raise SetupError("Choose an extracted prepared-base folder.")
    if path.stat().st_mode & 0o222:
        raise SetupError("This base is writable. Import an immutable copy; the original will stay unchanged.")
    lock = read_json(path / "device.lock.json")
    if lock.get("format") != 1 or lock.get("board") not in ("n72ap", "n81ap"):
        raise SetupError("This prepared-base format or board is unsupported.")
    if not isinstance(lock.get("build"), str) or not lock["build"]:
        raise SetupError("Prepared base is missing its iOS build identity.")
    board = lock["board"]
    strategy = lock.get("boot_strategy", "iboot" if board == "n72ap" else "kboot")
    if (board == "n81ap" and strategy != "kboot") or (board == "n72ap" and strategy not in ("iboot", "bootrom")):
        raise SetupError("This board's prepared boot route is unsupported.")
    return {"board": board, "build": lock["build"], "strategy": strategy,
            "base_identity": hashlib.sha256((path / "device.lock.json").read_bytes()).hexdigest()}


def regular(path, label):
    value = Path(path)
    if value.is_symlink() or not value.is_file():
        raise SetupError("Choose a regular " + label + " file.")


def validate(profile, allow_dirty=False):
    for field in ("name", "base", "state", "guest_package"):
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise SetupError("Complete the " + field.replace("_", " ") + " field.")
    base = absolute(profile["base"])
    state = absolute(profile["state"])
    disjoint(base, state)
    info = inspect_base(base)
    regular(absolute(profile["guest_package"]), "guest-tools package")
    if profile.get("bootrom"):
        regular(absolute(profile["bootrom"]), "boot ROM")
    if info["board"] == "n72ap" and not profile.get("bootrom"):
        raise SetupError("The current n72 route needs its matching boot asset. Select the verified file.")
    if info["board"] == "n81ap" and profile.get("bootrom"):
        raise SetupError("The n81 kboot route uses its prepared kboot.bin; clear the separate ROM field.")
    target = Path(state)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise SetupError("Writable state must be a regular folder, not a symbolic link.")
    pin = target / ".base-identity"
    if pin.exists() and (pin.is_symlink() or pin.read_text().strip() != info["base_identity"]):
        raise SetupError("This state belongs to another base. Choose its matching base or a new state folder.")
    if (target / ".dirty").exists() and not allow_dirty:
        raise SetupError("This state has an unconfirmed shutdown. Choose Recover interrupted state to cold boot it safely.")
    return info


def load_profile(path):
    value = read_json(path)
    if value.get("format") != FORMAT or value.get("version") != VERSION:
        raise SetupError("Choose a LightTouch Linux profile (version 1). Raw QEMU/Mac boot arguments need conversion first.")
    allowed = {"format", "version", "id", "name", "base", "state", "guest_package", "bootrom", "audio"}
    if set(value) - allowed:
        raise SetupError("Unsupported profile fields: " + ", ".join(sorted(set(value) - allowed)))
    result = {"id": uuid.uuid4().hex, "name": value.get("name", "Imported device"), "audio": value.get("audio", True)}
    if not isinstance(result["audio"], bool):
        raise SetupError("Audio must be true or false.")
    for field in ("base", "state", "guest_package", "bootrom"):
        result[field] = absolute(value[field], Path(path).absolute().parent) if value.get(field) else ""
    validate(result, allow_dirty=True)
    return result


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, staged = tempfile.mkstemp(prefix=".lighttouch-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
        directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


class Profiles:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.file = self.directory / "devices.json"
        self.devices = []
        self.selected = ""
        if self.file.exists():
            value = read_json(self.file)
            if value.get("version") != 1 or not isinstance(value.get("devices"), list):
                raise SetupError("Unsupported launcher library. Your files have been retained.")
            self.devices = value["devices"]
            self.selected = value.get("selected", "")
            for device in self.devices:
                if not isinstance(device, dict) or not re.fullmatch("[a-f0-9]{32}", device.get("id", "")):
                    raise SetupError("Invalid launcher device identity. Your files have been retained.")

    def save(self, profile):
        if not re.fullmatch("[a-f0-9]{32}", profile.get("id", "")):
            raise SetupError("Invalid launcher device identity.")
        self.devices = [p for p in self.devices if p["id"] != profile["id"]] + [dict(profile)]
        self.selected = profile["id"]
        self.persist()

    def persist(self):
        save_json(self.file, {"version": 1, "selected": self.selected, "devices": self.devices})

    def new_state(self, identity):
        if not re.fullmatch("[a-f0-9]{32}", identity):
            raise SetupError("Invalid launcher device identity.")
        return str(self.directory / "devices" / identity / "state")


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
