# Optional local real-asset cancellation regression; calls the production C API.
import argparse
import ctypes as c
import hashlib
import json
import pathlib

parser = argparse.ArgumentParser()
for name in ["library", "catalog", "stock", "tools", "inventory", "destination"]:
    parser.add_argument(name, type=pathlib.Path)
args = parser.parse_args()
callback = c.CFUNCTYPE(c.c_int, c.c_void_p, c.c_char_p, c.c_uint64, c.c_uint64)
class Request(c.Structure):
    _fields_ = [("size", c.c_uint32), ("version", c.c_uint32),
                ("catalog_path", c.c_char_p), ("firmware_id", c.c_char_p),
                ("ipsw_path", c.c_char_p), ("guest_tools_path", c.c_char_p),
                ("destination", c.c_char_p), ("guest_inventory_path", c.c_char_p),
                ("progress", callback), ("user", c.c_void_p)]
class Error(c.Structure):
    _fields_ = [("size", c.c_uint32), ("version", c.c_uint32), ("status", c.c_int32), ("message", c.c_char * 512)]
reached = False
@callback
def cancel(user, stage, done, total):
    global reached
    if stage == b"write NAND":
        reached = True
        return 1
    return 0

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()

assert not args.destination.exists()
old_stages = set(args.destination.parent.glob(f".{args.destination.name}.prepare-*"))
source_sha = sha(args.stock)
tool_hashes = {path.name: sha(path) for path in args.tools.iterdir() if path.is_file()}
request = Request(c.sizeof(Request), 1, str(args.catalog).encode(), b"n72ap-7E18", str(args.stock).encode(),
                  str(args.tools).encode(), str(args.destination).encode(), str(args.inventory).encode(), cancel, None)
error = Error(c.sizeof(Error), 1)
library = c.CDLL(str(args.library))
library.lt_firmware_prepare.argtypes = [c.POINTER(Request), c.POINTER(Error)]
library.lt_firmware_prepare.restype = c.c_int
status = library.lt_firmware_prepare(c.byref(request), c.byref(error))
assert reached and status == error.status == 6, error.message
assert not args.destination.exists()
assert set(args.destination.parent.glob(f".{args.destination.name}.prepare-*")) == old_stages
assert sha(args.stock) == source_sha
assert {path.name: sha(path) for path in args.tools.iterdir() if path.is_file()} == tool_hashes
print(json.dumps({"cancel_after_system_edits": "PASS", "stage_removed": True, "sources_unchanged": True,
                  "guest_boot": "NOT_TESTED", "android_preparation": "NOT_TESTED"}, sort_keys=True))
