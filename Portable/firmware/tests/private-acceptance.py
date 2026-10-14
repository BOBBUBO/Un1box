# Optional local-only acceptance. Real firmware paths must stay outside Git.
import argparse
import hashlib
import json
import pathlib

parser = argparse.ArgumentParser()
parser.add_argument("prepared", type=pathlib.Path)
args = parser.parse_args()
base = args.prepared.resolve()
lock = json.loads((base / "device.lock.json").read_text())
assert lock["format"] == 1 and lock["board"] == "n72ap" and lock["build"] == "7E18"
assert lock["boot_strategy"] == "iboot"
assert lock["guest_package"]["family"] == "n72-ios3"
assert "/usr/lib/it_typein.dylib" in lock["guest_package"]["hooks"]
assert lock["derived"]["kernelcache_path"] == "System/Library/Caches/com.apple.kernelcaches/kernelcache.s5l8720x"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert lock["identity"]["sha256"] == sha(base / "identity.json")
for name in ["nor", "iboot", "gid_blobs"]:
    entry = lock["outputs"][name]
    assert entry["sha256"] == sha(base / entry["path"])
nand = lock["outputs"]["nand"]
files = sorted(path for path in (base / nand["path"]).rglob("*") if path.is_file())
assert len(files) == nand["pages"]
listing = hashlib.sha256()
for path in files:
    assert not path.is_symlink() and path.stat().st_size == 4160
    listing.update(f"{path.relative_to(base / nand['path'])} {sha(path)}\n".encode())
assert listing.hexdigest() == nand["listing_sha256"] == nand["built_listing_sha256"]
checks = json.loads((base / "preparation.sha256.json").read_text())
assert checks["version"] == 1
for name, expected in checks["files"].items():
    path = base / name
    assert path.resolve().is_relative_to(base)
    assert sha(path) == expected
print(json.dumps({"prepared_metadata": "PASS", "pages": len(files), "nand_listing_sha256": listing.hexdigest(),
                  "guest_boot": "NOT_TESTED", "android_preparation": "NOT_TESTED"}, sort_keys=True))
