"""Private launcher profiles. Native admission remains authoritative for guest assets."""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .library import RecordError, check_collection, check_update, display_title
from .storage import DEFAULT_STORAGE

FORMAT = "lighttouch-linux-profile"
VERSION = 1
MAX_CONFIG_BYTES = 1024 * 1024


class SetupError(ValueError):
    pass


def _config_bytes(path, storage):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SetupError("Choose a regular configuration file, not a symbolic link.")
    try:
        with os.fdopen(storage.open_regular(path), "rb") as stream:
            data = stream.read(MAX_CONFIG_BYTES + 1)
    except OSError as error:
        raise SetupError("Cannot securely open configuration: " + str(error)) from error
    if len(data) > MAX_CONFIG_BYTES:
        raise SetupError("Configuration is larger than 1 MiB.")
    return data


def _decode_json(data):
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


def read_json(path, *, storage=None):
    return _decode_json(_config_bytes(path, storage or DEFAULT_STORAGE))


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


def inspect_base(base, *, storage=None):
    """Cheap setup metadata only. Does not replace native complete integrity admission."""
    path = Path(base)
    storage = storage or DEFAULT_STORAGE
    if path.is_symlink() or not path.is_dir():
        raise SetupError("Choose an extracted prepared-base folder.")
    attributes = path.stat()
    if not storage.immutable_base(path, attributes):
        raise SetupError("This base is writable. Import an immutable copy; the original will stay unchanged.")
    data = _config_bytes(path / "device.lock.json", storage)
    lock = _decode_json(data)
    if lock.get("format") != 1 or lock.get("board") not in ("n72ap", "n81ap"):
        raise SetupError("This prepared-base format or board is unsupported.")
    if not isinstance(lock.get("build"), str) or not lock["build"]:
        raise SetupError("Prepared base is missing its iOS build identity.")
    board = lock["board"]
    strategy = lock.get("boot_strategy", "iboot" if board == "n72ap" else "kboot")
    if (board == "n81ap" and strategy != "kboot") or (board == "n72ap" and strategy not in ("iboot", "bootrom")):
        raise SetupError("This board's prepared boot route is unsupported.")
    return {"board": board, "build": lock["build"], "strategy": strategy,
            "base_identity": hashlib.sha256(data).hexdigest()}


def regular(path, label, *, storage=None):
    value = Path(path)
    if value.is_symlink() or not value.is_file():
        raise SetupError("Choose a regular " + label + " file.")
    try:
        os.close((storage or DEFAULT_STORAGE).open_regular(value))
    except OSError as error:
        raise SetupError("Cannot securely open " + label + ": " + str(error)) from error


def validate(profile, allow_dirty=False, *, storage=None):
    storage = storage or DEFAULT_STORAGE
    for field in ("name", "base", "state", "guest_package"):
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise SetupError("Complete the " + field.replace("_", " ") + " field.")
    base = absolute(profile["base"])
    state = absolute(profile["state"])
    disjoint(base, state)
    info = inspect_base(base, storage=storage)
    regular(absolute(profile["guest_package"]), "guest-tools package", storage=storage)
    if profile.get("bootrom"):
        regular(absolute(profile["bootrom"]), "boot ROM", storage=storage)
    if info["board"] == "n72ap" and not profile.get("bootrom"):
        raise SetupError("The current n72 route needs its matching boot asset. Select the verified file.")
    if info["board"] == "n81ap" and profile.get("bootrom"):
        raise SetupError("The n81 kboot route uses its prepared kboot.bin; clear the separate ROM field.")
    target = Path(state)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise SetupError("Writable state must be a regular folder, not a symbolic link.")
    pin = target / ".base-identity"
    if pin.exists() or pin.is_symlink():
        regular(pin, "state identity", storage=storage)
        with os.fdopen(storage.open_regular(pin), "rb") as stream:
            content = stream.read(129)
            matches = len(content) <= 128 and content.strip() == info["base_identity"].encode("ascii")
        if not matches:
            raise SetupError("This state belongs to another base. Choose its matching base or a new state folder.")
    dirty = target / ".dirty"
    if (dirty.exists() or dirty.is_symlink()) and not allow_dirty:
        raise SetupError("This state has an unconfirmed shutdown. Choose Recover interrupted state to cold boot it safely.")
    return info


def load_profile(path, *, storage=None):
    value = read_json(path, storage=storage)
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
    validate(result, allow_dirty=True, storage=storage)
    return result


def save_json(path, value, *, storage=None):
    storage = storage or DEFAULT_STORAGE
    path = Path(path)
    storage.ensure_directory(path.parent)
    fd, staged = storage.create_temp(path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            storage.flush_file(stream.fileno())
        storage.replace_and_sync(staged, path)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


class Profiles:
    def __init__(self, directory, *, storage=None):
        self.storage = storage or DEFAULT_STORAGE
        self.directory = Path(directory)
        self.file = self.directory / "devices.json"
        self.devices = []
        self.selected = ""
        if self.file.exists():
            value = read_json(self.file, storage=self.storage)
            if value.get("version") != 1 or not isinstance(value.get("devices"), list):
                raise SetupError("Unsupported launcher library. Your files have been retained.")
            self.devices = value["devices"]
            self.selected = value.get("selected", "")
            try:
                check_collection(self.devices, self.selected, lambda value: Path(absolute(value)).resolve())
            except RecordError as error:
                raise SetupError(str(error)) from error

    def save(self, profile):
        try:
            check_update(profile, self.devices, lambda value: Path(absolute(value)).resolve())
        except RecordError as error:
            raise SetupError(str(error)) from error
        devices = [p for p in self.devices if p["id"] != profile["id"]] + [dict(profile)]
        selected = profile["id"]
        save_json(self.file, {"version": 1, "selected": selected, "devices": devices}, storage=self.storage)
        self.devices, self.selected = devices, selected

    def persist(self):
        save_json(self.file, {"version": 1, "selected": self.selected, "devices": self.devices}, storage=self.storage)

    def new_state(self, identity):
        if not re.fullmatch("[a-f0-9]{32}", identity):
            raise SetupError("Invalid launcher device identity.")
        return str(self.directory / "devices" / identity / "state")

