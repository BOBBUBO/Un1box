#!/usr/bin/env python3
"""Name ARMv6 guest TB addresses from a matching kernel and dyld v1 cache.

Only code-section ranges are named; symbol extents end at the next symbol or
section boundary. Indirect stubs are labeled separately. Addresses are unslid.
This does not identify processes, unwind guest stacks, or give instruction PCs
within a sampled translation block. Firmware inputs stay local.
"""
import argparse
import bisect
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import struct


def macho_symbols(data, base, fallback):
    if struct.unpack_from("<I", data, base)[0] != 0xfeedface:
        raise ValueError("expected little-endian 32-bit Mach-O")
    ncmds, sizeofcmds = struct.unpack_from("<II", data, base + 16)
    if ncmds > 4096 or base + 28 + sizeofcmds > len(data):
        raise ValueError("invalid Mach-O load-command span")
    pos, sections, symtab, indirect, name = base + 28, [], None, None, fallback
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, pos)
        if size < 8 or pos + size > base + 28 + sizeofcmds:
            raise ValueError("invalid load command")
        if cmd == 1:  # LC_SEGMENT
            nsects = struct.unpack_from("<I", data, pos + 48)[0]
            if 56 + nsects * 68 > size:
                raise ValueError("invalid Mach-O section span")
            for i in range(nsects):
                offset = pos + 56 + i * 68
                lo, length = struct.unpack_from("<II", data, offset + 32)
                flags, reserved1, reserved2 = struct.unpack_from("<III", data, offset + 56)
                sections.append((lo, lo + length, flags, reserved1, reserved2))
        elif cmd == 2:  # LC_SYMTAB: cache file offsets are already absolute
            symtab = struct.unpack_from("<IIII", data, pos + 8)
        elif cmd == 0xb:  # LC_DYSYMTAB
            indirect = struct.unpack_from("<II", data, pos + 56)
        elif cmd == 0xd:  # LC_ID_DYLIB: canonical identity, not cache alias path
            offset = struct.unpack_from("<I", data, pos + 8)[0]
            if offset < 24 or offset >= size:
                raise ValueError("invalid dylib name")
            name = data[pos + offset:data.index(0, pos + offset, pos + size)].decode("latin1")
        pos += size
    if not symtab:
        return []
    symoff, count, stroff, strsize = symtab
    if symoff + count * 12 > len(data) or stroff + strsize > len(data):
        raise ValueError("symbol table outside input")

    def symbol(index):
        if index >= count:
            raise ValueError("indirect symbol index outside symbol table")
        strx, typ, sect, desc, value = struct.unpack_from("<IBBHI", data, symoff + index * 12)
        if strx >= strsize:
            raise ValueError("symbol string outside table")
        end = data.index(0, stroff + strx, stroff + strsize)
        return typ, sect, value & ~1, data[stroff + strx:end].decode("latin1")

    groups = defaultdict(lambda: defaultdict(set))
    for i in range(count):
        typ, sect, value, label = symbol(i)
        if typ & 0xe0 or typ & 0xe != 0xe or not 0 < sect <= len(sections):
            continue  # ignore debug, undefined, absolute and non-section entries
        lo, hi, flags, *_ = sections[sect - 1]
        if flags & 0x80000400 and lo <= value < hi:
            groups[sect][value].add(label)
    output = []
    for sect, rows in groups.items():
        addresses = sorted(rows)
        for i, address in enumerate(addresses):
            end = addresses[i + 1] if i + 1 < len(addresses) else sections[sect - 1][1]
            output.append((address, end, name, "|".join(sorted(rows[address]))))
    if indirect:
        table, length = indirect
        if table + length * 4 > len(data):
            raise ValueError("indirect table outside input")
        for lo, hi, flags, first, stride in sections:
            if flags & 0xff != 8:  # S_SYMBOL_STUBS
                continue
            if not stride or (hi - lo) % stride or first + (hi - lo) // stride > length:
                raise ValueError("invalid indirect stub section")
            for i, address in enumerate(range(lo, hi, stride)):
                index = struct.unpack_from("<I", data, table + (first + i) * 4)[0]
                if index & 0xc0000000:
                    continue  # INDIRECT_SYMBOL_LOCAL / ABS
                output.append((address, address + stride, name, "stub:" + symbol(index)[3]))
    return output


class GuestSymbols:
    def __init__(self, kernel, cache):
        kernel_data, cache_data = kernel.read_bytes(), cache.read_bytes()
        self.hashes = {"kernel": hashlib.sha256(kernel_data).hexdigest(),
                       "dyld_cache": hashlib.sha256(cache_data).hexdigest()}
        ranges = macho_symbols(kernel_data, 0, "kernel")
        if cache_data[:16].rstrip(b"\0") != b"dyld_v1   armv6":
            raise ValueError("expected ARMv6 dyld v1 cache")
        mapping_offset, mapping_count, image_offset, image_count = struct.unpack_from("<4I", cache_data, 16)
        if mapping_count > 32 or image_count > 4096:
            raise ValueError("invalid dyld cache counts")
        mappings = [struct.unpack_from("<3Q2I", cache_data, mapping_offset + i * 32)
                    for i in range(mapping_count)]
        seen = set()
        for i in range(image_count):
            address, _, _, path_offset, _ = struct.unpack_from("<3Q2I", cache_data, image_offset + i * 32)
            if address in seen:
                continue  # libSystem has multiple cache aliases with the same Mach-O
            seen.add(address)
            path = cache_data[path_offset:cache_data.index(0, path_offset)].decode("latin1")
            offsets = [offset + address - va for va, size, offset, _, _ in mappings
                       if va <= address < va + size]
            if len(offsets) != 1:
                raise ValueError("dyld image has no unique mapping")
            ranges.extend(macho_symbols(cache_data, offsets[0], path))
        # Explicit stub ranges override any nearby exported symbol approximation.
        unique = {}
        for row in sorted(set(ranges), key=lambda r: r[3].startswith("stub:")):
            unique[(row[0], row[1])] = row
        self.ranges = sorted(unique.values())
        self.starts = [row[0] for row in self.ranges]
        self.max_ends = []
        end = 0
        for row in self.ranges:
            end = max(end, row[1])
            self.max_ends.append(end)

    def lookup(self, pc):
        index = bisect.bisect_right(self.starts, pc) - 1
        matches = []
        while index >= 0 and self.max_ends[index] > pc:
            row = self.ranges[index]
            if row[0] <= pc < row[1]:
                matches.append(row)
            index -= 1
        stubs = [row for row in matches if row[3].startswith("stub:")]
        chosen = stubs or matches
        names = {f"{row[2]}:{row[3]}" for row in chosen}
        return next(iter(names)) if len(names) == 1 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kernel", type=Path, required=True)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--pc", action="append", default=[])
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    symbols = GuestSymbols(args.kernel, args.cache)
    print(f"{len(symbols.ranges)} named code ranges; input SHA256: {symbols.hashes}")
    for pc in args.pc:
        print(f"{pc}: {symbols.lookup(int(pc, 0)) or 'unknown/ambiguous'}")
    if args.json:
        args.json.write_text(json.dumps(dict(input_sha256=symbols.hashes,
                                              code_ranges=symbols.ranges), indent=2) + "\n")


if __name__ == "__main__":
    main()
