# Optional local-only acceptance. Real firmware paths must stay outside Git.
import argparse
import hashlib
import json
import pathlib
import struct

parser = argparse.ArgumentParser()
parser.add_argument("prepared", type=pathlib.Path)
args = parser.parse_args()
base = args.prepared.resolve()
lock = json.loads((base / "device.lock.json").read_text())
assert lock["format"] == 1 and lock["board"] == "n72ap" and lock["build"] == "7E18"
assert lock["boot_strategy"] == "iboot"
assert lock["guest_package"]["itpack"]["path"] == "guest.itpack"
assert lock["guest_package"]["family"] == "n72-ios3"
assert "/usr/lib/it_typein.dylib" in lock["guest_package"]["hooks"]
assert lock["derived"]["kernelcache_path"] == "System/Library/Caches/com.apple.kernelcaches/kernelcache.s5l8720x"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert lock["identity"]["sha256"] == sha(base / "identity.json")
for name in ["nor", "iboot", "gid_blobs", "guest_package"]:
    entry = lock["outputs"][name]
    assert entry["sha256"] == sha(base / entry["path"])
nand = lock["outputs"]["nand"]
# The NAND is one ITNAND01 file: a 24-byte header, 4 x 524288 little-endian record numbers, records.
packed = (base / nand["path"]).read_bytes()
assert nand["format"] == "ITNAND01" and packed[:8] == b"ITNAND01" and "listing_sha256" not in nand
assert struct.unpack_from("<4I", packed, 8) == (4096, 64, 4, 524288)
assert nand["sha256"] == hashlib.sha256(packed).hexdigest()
records_at = 24 + 4 * 524288 * 4
assert (len(packed) - records_at) % 4160 == 0 and (len(packed) - records_at) // 4160 == nand["pages"]
slots = struct.unpack_from("<%dI" % (4 * 524288), packed, 24)
pages = {}
for index, record in enumerate(slots):
    if record:
        at = records_at + (record - 1) * 4160
        pages[f"cs{index // 524288}/{index % 524288}.page"] = hashlib.sha256(packed[at:at + 4160]).hexdigest()
assert len(pages) == nand["pages"] == len(set(r for r in slots if r))
listing = hashlib.sha256("".join(f"{name} {pages[name]}\n" for name in sorted(pages)).encode())
assert listing.hexdigest() == nand["source_listing_sha256"]
files = pages
checks = json.loads((base / "preparation.sha256.json").read_text())
assert checks["version"] == 1
for name, expected in checks["files"].items():
    path = base / name
    assert path.resolve().is_relative_to(base)
    assert sha(path) == expected
print(json.dumps({"prepared_metadata": "PASS", "pages": len(files), "nand_listing_sha256": listing.hexdigest(),
                  "guest_boot": "NOT_TESTED", "android_preparation": "NOT_TESTED"}, sort_keys=True))
