#!/usr/bin/env python3
"""Read-only firmware/guest-package inventory. Never downloads, prepares or boots."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import zlib

BOARDS = {
    "m68ap": ("iPhone", "m68", "armv6", "bootrom_s5l8900"),
    "n45ap": ("iPod touch (1st generation)", "n45", "armv6", "bootrom_s5l8900"),
    "n72ap": ("iPod touch (2nd generation)", "n72", "armv6", "bootrom_240_4"),
    "n18ap": ("iPod touch (3rd generation)", "n18", "armv7", None),
    "n81ap": ("iPod touch (4th generation)", "n81", "armv7", None),
    "n88ap": ("iPhone 3GS", "n88", "armv7", None),
    "n90ap": ("iPhone 4", "n90", "armv7", None),
    "k48ap": ("iPad", "k48", "armv7", None),
}
REPRESENTATIVE_RELEASES = (
    "n45ap-4A102", "n72ap-5H11a", "n72ap-7E18", "n81ap-8C148",
    "n81ap-9B206", "n81ap-10B500", "n90ap-11D257",
)
MAX_INDEX = 8 * 1024 * 1024
MAX_GUEST_STREAM = 256 * 1024 * 1024


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_index(path):
    """Index only: no extraction, decompression, firmware hashing or writes."""
    with Path(path).open("rb") as f:
        header = f.read(12)
        if len(header) != 12 or header[:8] != b"ITPACK01":
            raise ValueError("not an ITPACK01 prepared base or guest package")
        size = struct.unpack("<I", header[8:])[0]
        if size > MAX_INDEX:
            raise ValueError("index exceeds 8 MiB metadata bound")
        data = f.read(size)
        if len(data) != size:
            raise ValueError("truncated index")
        entries = json.loads(data)["entries"]
    seen = set()
    for e in entries:
        name = e["name"]
        if (not isinstance(name, str) or name.startswith("/") or "\\" in name
                or any(p in ("", ".", "..") for p in name.split("/"))
                or name in seen or type(e["size"]) is not int or e["size"] < 0):
            raise ValueError("invalid or duplicate index entry")
        seen.add(name)
    return entries, 12 + size


def read_guest_manifests(path):
    """Guest package only, bounded decompression; no firmware files extracted."""
    entries, offset = read_index(path)
    expected = sum(e["size"] for e in entries)
    if expected > MAX_GUEST_STREAM:
        raise ValueError("guest stream exceeds 256 MiB bound; do not pass .itbase here")
    stream = bytearray()
    decoder = zlib.decompressobj()
    with Path(path).open("rb") as f:
        f.seek(offset)
        for block in iter(lambda: f.read(1024 * 1024), b""):
            stream.extend(decoder.decompress(block, expected + 1 - len(stream)))
            if len(stream) > expected or decoder.unconsumed_tail:
                raise ValueError("stream exceeds indexed size")
            if decoder.unused_data:
                raise ValueError("trailing compressed data")
    if not decoder.eof or len(stream) != expected:
        raise ValueError("truncated stream or indexed size mismatch")
    manifests = []
    cursor = 0
    for e in entries:
        end = cursor + e["size"]
        if e["name"].endswith("/manifest.json"):
            m = json.loads(stream[cursor:end])
            # Keep compatibility metadata only; no embedded binaries/keys.
            manifests.append({k: m[k] for k in ("family", "arch", "serial", "version", "requires", "stub") if k in m})
        cursor = end
    if not manifests:
        raise ValueError("no guest manifests; this may be a prepared base, not an .itpack")
    return {"sha256": digest(path), "bytes": Path(path).stat().st_size, "manifests": manifests}


def build_matches(patterns, build):
    # Match HostRuntime/GuestPack.swift: wildcard matches the complete leading number.
    major = re.match(r"[0-9]*", build).group()
    return any(p == build or (p.endswith("*") and p[:-1] == major) for p in patterns)


def boot_contract(entry):
    board = entry["board"]
    _, _, arch, rom = BOARDS[board]
    recipe = entry["recipe"]
    if board in ("m68ap", "n45ap"):
        strategy, files = "iboot", ["iBoot.bin", "nor.bin"]
    elif board == "n72ap":
        major = int(entry["version"].split(".")[0])
        strategy = "iboot" if major >= 3 else "bootrom"
        files = ["nor.bin", "gid-blobs.bin"] + (["iBoot.bin"] if major >= 3 else [])
    else:
        strategy = recipe.get("boot", "iboot" if board == "k48ap" else "kboot")
        if strategy == "iboot" and board == "k48ap":
            files = ["iBoot.bin", "nor.bin", "gid-blobs.bin"]
        elif strategy == "kboot":
            nor = recipe.get("options", {}).get("writable_nor", False) or board in ("n88ap", "n18ap")
            files = ["kboot.bin"] + (["nor.bin"] if nor else [])
        else:
            raise ValueError("unsupported catalog boot strategy: " + strategy)
    return {"strategy": strategy, "files_from_preparation": files + ["nand/", "identity.json", "device.lock.json"],
            "external_rom": rom, "guest_arch": arch, "guest_pack": arch + ".itpack",
            "authority": "recipe source prediction; require actual device.lock.json and runtime validation"}


def enumerate_catalog(catalog, packs=(), all_catalog=False, stable_only=False):
    raw = Path(catalog).read_bytes()
    data = json.loads(raw)
    rows, seen = [], set()
    ipsws, downloads = {}, {}
    if all_catalog and stable_only:
        raise ValueError("choose historical all-catalog or stable-only, not both")
    stable = [e for e in data["entries"] if e.get("status") == "available"]
    representatives = [e for e in data["entries"]
                       if e["id"] in REPRESENTATIVE_RELEASES and e.get("status") != "available"]
    if not all_catalog and not stable_only:
        present = {e["id"] for e in data["entries"]}
        if set(REPRESENTATIVE_RELEASES) - present:
            raise ValueError("catalog misses explicitly selected representative releases")
        if any(e.get("status") != "experimental" or e.get("prerelease") for e in representatives):
            raise ValueError("representative selection must remain documented experimental releases; re-review changed catalog")
    selected = data["entries"] if all_catalog else stable + ([] if stable_only else representatives)
    for entry in selected:
        ident, board = entry["id"], entry["board"]
        if ident in seen or not re.fullmatch(r"[a-z0-9]+-[A-Za-z0-9]+", ident):
            raise ValueError("duplicate or unsafe entry id: " + ident)
        seen.add(ident)
        if board not in BOARDS or entry["recipe"]["name"] != BOARDS[board][1]:
            raise ValueError("unknown board/recipe: " + ident)
        source = entry["source"]
        if source["kind"] not in ("ipsw", "rar"):
            raise ValueError("unknown source kind: " + ident)
        sha = source.get("sha1", "")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise ValueError("missing IPSW SHA-1: " + ident)
        size = source.get("bytes", 0)
        if type(size) is not int or size <= 0:
            raise ValueError("missing IPSW size: " + ident)
        if sha in ipsws and ipsws[sha] != size:
            raise ValueError("conflicting IPSW sizes for hash: " + sha)
        ipsws[sha] = size
        transport_sha, transport_size = sha, size
        if source["kind"] == "rar":
            transport_sha = source.get("archive_sha1", "")
            transport_size = source.get("archive_bytes", 0)
            if (not re.fullmatch(r"[0-9a-fA-F]{40}", transport_sha)
                    or type(transport_size) is not int or transport_size <= 0
                    or not source.get("member")):
                raise ValueError("missing wrapped-source hash/size/member: " + ident)
        if transport_sha in downloads and downloads[transport_sha] != transport_size:
            raise ValueError("conflicting transport sizes for hash: " + transport_sha)
        downloads[transport_sha] = transport_size
        contract = boot_contract(entry)
        matched = [{"pack_sha256": p["sha256"], "family": m["family"], "serial": m.get("serial"),
                    "version": m.get("version"), "stub": bool(m.get("stub", False))}
                   for p in packs for m in p["manifests"]
                   if m["arch"] == contract["guest_arch"] and board in m["requires"]["boards"]
                   and build_matches(m["requires"]["builds"], entry["build"])]
        notes = []
        if board in ("m68ap", "n45ap") and not entry["version"].startswith("1."):
            notes.append("Catalog contains later versions; current N45 recipe and guest family implement 1.x only.")
        if packs and len(matched) != 1:
            notes.append("Expected exactly one matching guest package; found " + str(len(matched)))
        if any(m["stub"] for m in matched):
            notes.append("Matching guest family is a stub; metadata match does not provide guest services.")
        rows.append({"id": ident, "board": board, "device": BOARDS[board][0],
                     "product_type": entry["product_type"], "version": entry["version"], "build": entry["build"],
                     "upstream_status": entry["status"], "source": source,
                     "cohort": ("stable_available" if entry["status"] == "available" else
                                "representative_ios_major" if ident in REPRESENTATIVE_RELEASES else "historical_only"),
                     "representative_major": (int(entry["version"].split(".")[0]) if ident in REPRESENTATIVE_RELEASES else None),
                     "prerelease": entry.get("prerelease"),
                     "prerelease_number": entry.get("prerelease_number"),
                     "released": entry.get("released"),
                     "emulator_requirement": entry.get("emulator", {}),
                     "immutable_base": {"reference": None, "sha256": None, "lock_sha256": None, "status": "not_assessed"},
                     "key_component_count": len(entry.get("keys", {})), "recipe": entry["recipe"],
                     "estimates": entry.get("estimates", {}), "boot": contract,
                     "sibling_entry": entry["recipe"].get("keybag_ramdisk_from"),
                     "matching_guest_packages": matched, "notes": notes,
                     "prepared": "not_assessed", "target_acceptance": {
                         "linux": "not_assessed", "android": "not_assessed",
                         "windows": "not_assessed", "macos": "not_assessed"}})
    for row in rows:
        if row["sibling_entry"] and row["sibling_entry"] not in seen:
            raise ValueError("missing sibling entry: " + row["sibling_entry"])
    return {"schema": 1, "catalog_sha256": hashlib.sha256(raw).hexdigest(),
            "catalog_format": data["format"], "catalog_entry_count": len(data["entries"]),
            "selection": ("historical_all_catalog" if all_catalog else "upstream_available_only" if stable_only else
                          "stable_first_and_representative_releases"), "entry_count": len(rows),
            "deduplicated_ipsw_count": len(ipsws), "deduplicated_ipsw_bytes": sum(ipsws.values()),
            "deduplicated_download_bytes": sum(downloads.values()),
            "catalog_prepared_estimate_bytes": sum(r["estimates"].get("prepared_bytes", 0) for r in rows),
            "guest_packages": list(packs), "entries": rows,
            "limitations": "Catalog/manifest matching is inventory only; never target boot or feature acceptance."}


def attach_lock_metadata(inventory, paths):
    """Attach observed lock metadata only; never infer validated base contents or acceptance."""
    rows = {row["id"]: row for row in inventory["entries"]}
    attached = set()
    for path in paths:
        path = Path(path).resolve()
        raw = path.read_bytes()
        lock = json.loads(raw)
        ident = str(lock.get("board", "")) + "-" + str(lock.get("build", ""))
        if lock.get("format") != 1 or ident not in rows or ident in attached:
            raise ValueError("unknown, duplicate or invalid prepared lock: " + ident)
        row = rows[ident]
        strategy = lock.get("boot_strategy", row["boot"]["strategy"])
        if strategy != row["boot"]["strategy"]:
            raise ValueError("prepared lock boot strategy disagrees with catalog recipe: " + ident)
        package = lock.get("guest_package") or {}
        row["immutable_base"] = {
            "reference": str(path.parent), "sha256": None,
            "lock_sha256": hashlib.sha256(raw).hexdigest(), "status": "lock_metadata_only"}
        row["prepared_guest_package"] = {
            "family": package.get("family"), "version": package.get("version"),
            "serial": package.get("seed"), "pack_sha256": (package.get("itpack") or {}).get("sha256"),
            "status": "declared_by_lock_not_payload_verified"}
        row["notes"].append("Observed lock metadata only; base content integrity and runtime acceptance remain unassessed.")
        attached.add(ident)
    return inventory


def preparation_plan(catalog, ident, ipsw, root, firmwarekit, helper, guest_tools):
    inventory = enumerate_catalog(catalog)
    row = next((e for e in inventory["entries"] if e["id"] == ident), None)
    if row is None:
        raise ValueError("entry not found: " + ident)
    root = Path(root)
    if not root.is_absolute() or not Path(ipsw).is_absolute():
        raise ValueError("use absolute paths for root and IPSW")
    staging = str(root / "staging" / ident)
    command = [firmwarekit, "create", "--catalog", str(Path(catalog).resolve()), "--id", ident,
               "--ipsw", ipsw, "--out", staging, "--cache", str(root / "decrypt-cache"),
               "--seed", "matrix-" + ident, "--language", "en-US"]
    if helper:
        command += ["--helper", helper]
    if guest_tools:
        command += ["--guest-tools", guest_tools]
    return {"execution_authorized": False, "catalog_sha256": inventory["catalog_sha256"],
            "entry": row, "create_argv": command,
            "pack_argv": [firmwarekit, "pack-base", "--base", staging,
                          "--out", str(root / "packed" / (ident + ".itbase"))],
            "blockers": (["Supply --sibling-entry and --sibling-ipsw for " + row["sibling_entry"]]
                         if row["sibling_entry"] else []) + row["notes"],
            "environment": {"LTM_STATE_DIR": str(root / "preparation-service-state")},
            "requirements": ["Use a new empty private staging directory outside Git; never reuse a source fixture.",
                             "Verify supplied IPSW size/SHA-1 against entry; capture tool/engine/guest-pack hashes.",
                             "Admit disk and RAM; serialize preparation and any one-shot boots with Mac owner.",
                             "Apply an external deadline with cancellation and child/image retirement checks.",
                             "Require done event and inspect lock/fit/warnings before pack-base.",
                             "Pack only after successful preparation; keep base read-only and state isolated.",
                             "Regenerate endpoints/package offers per host; qualify each platform independently."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    enum = sub.add_parser("enumerate")
    enum.add_argument("--catalog", required=True)
    enum.add_argument("--guest-pack", action="append", default=[])
    enum.add_argument("--prepared-lock", action="append", default=[])
    selection = enum.add_mutually_exclusive_group()
    selection.add_argument("--all-catalog", action="store_true", help="Historical read-only inventory, never preparation selection.")
    selection.add_argument("--stable-only", action="store_true", help="The six upstream available entries only.")
    inspect = sub.add_parser("inspect-index")
    inspect.add_argument("blob")
    plan = sub.add_parser("plan")
    plan.add_argument("--catalog", required=True)
    plan.add_argument("--id", required=True)
    plan.add_argument("--ipsw", required=True)
    plan.add_argument("--root", required=True)
    plan.add_argument("--firmwarekit", required=True)
    plan.add_argument("--helper")
    plan.add_argument("--guest-tools")
    args = parser.parse_args()
    try:
        if args.command == "enumerate":
            result = attach_lock_metadata(
                enumerate_catalog(args.catalog, [read_guest_manifests(p) for p in args.guest_pack], args.all_catalog, args.stable_only), args.prepared_lock)
        elif args.command == "inspect-index":
            entries, _ = read_index(args.blob)
            result = {"entries": entries, "logical_bytes": sum(e["size"] for e in entries),
                      "integrity": "index only; compressed payload not validated"}
        else:
            result = preparation_plan(args.catalog, args.id, args.ipsw, args.root,
                                      args.firmwarekit, args.helper, args.guest_tools)
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        print()
    except (ValueError, KeyError, OSError, TypeError, zlib.error) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
