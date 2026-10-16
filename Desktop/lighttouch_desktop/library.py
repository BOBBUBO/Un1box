"""Record rules shared by desktop adapters, using existing profile/native identities.

No persistence format, copying, guest execution or OS locking lives here.
Callers supply host path canonicalization; native admission remains authoritative.
"""
import re


class RecordError(ValueError):
    pass


def _path(record, field, canonicalize):
    value = record.get(field)
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise RecordError("Complete the " + field + " field.")
    return canonicalize(value)


def _overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def check_update(record, existing, canonicalize):
    """Keep writable identity stable and separate; sharing an immutable base is OK."""
    identity = record.get("id", "")
    if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{32}", identity):
        raise RecordError("Invalid launcher device identity.")
    if not isinstance(record.get("name"), str) or not record["name"].strip():
        raise RecordError("Give this configuration a name.")
    base, state = (_path(record, field, canonicalize) for field in ("base", "state"))
    if _overlap(base, state):
        raise RecordError("Writable state must be separate from the prepared base.")
    matches = [other for other in existing if other.get("id") == identity]
    if len(matches) > 1:
        raise RecordError("Duplicate saved configuration identity; existing files have been retained.")
    for other in existing:
        old_base, old_state = (_path(other, field, canonicalize) for field in ("base", "state"))
        if other.get("id") == identity:
            if base != old_base or state != old_state:
                raise RecordError("This saved configuration keeps its system and writable state. Add a new configuration to use another system or state.")
        elif _overlap(state, old_state):
            raise RecordError("Each configuration needs its own writable state folder.")
        if _overlap(state, old_base) or _overlap(base, old_state):
            raise RecordError("Writable state must be separate from every saved prepared base.")


def check_collection(records, selected, canonicalize):
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise RecordError("Unsupported launcher library. Your files have been retained.")
    identities = [record.get("id") for record in records]
    if any(not isinstance(identity, str) for identity in identities):
        raise RecordError("Invalid launcher device identity.")
    if len(set(identities)) != len(identities):
        raise RecordError("Duplicate saved configuration identity; existing files have been retained.")
    if selected and selected not in identities:
        raise RecordError("Selected configuration is missing; existing files have been retained.")
    for record in records:
        check_update(record, records, canonicalize)


def display_title(name, board, build):
    # Display known supplied build identities; this table never grants support.
    versions = {("n72ap", "7E18"): "3.1.3", ("n81ap", "8C148"): "4.2.1"}
    version = versions.get((board, build))
    system = "iOS " + version + " (" + build + ")" if version else build
    device = {"n72ap": "iPod touch (2nd generation)", "n81ap": "iPod touch (4th generation)"}.get(board, "Prepared device")
    return " · ".join([name, device, system, "prepared"])
