#!/usr/bin/env python3
"""Get the iPod touch 2G / iOS 3.1.3 (n72ap-7E18) files LightTouch needs.

Downloads the public upstream LightTouchMac app ZIP (or uses one you already
have), checks its SHA-256, and writes into OUT:

  n72ap-7E18.itbase   the prepared iOS system
  bootrom.bin         the n72 SecureROM (upstream's bootrom_240_4)
  armv6.itpack        the guest tools, unpacked from the app's guest.aar
  armv7.itpack        the iPod touch 4G guest tools (only with --armv7)

Python 3.8+ standard library only; works on Linux, Windows and macOS. The app's
guest.aar is an Apple Archive (AA01 entries in a 'pbze' LZFSE block stream);
this file includes a small LZFSE decoder for it, so no Apple tools are needed.

  python3 tools/get-assets.py                       # download, then extract
  python3 tools/get-assets.py --zip LightTouchMac-universal.zip
  python3 tools/get-assets.py --list-aar guest.aar  # inspect any guest.aar
"""
import argparse, hashlib, json, os, struct, sys, urllib.request, zipfile

RELEASE = 'https://github.com/samhenrigold/LightTouchMac/releases/download/v1.0-2026.10.15b/LightTouchMac-universal.zip'
RELEASE_SHA256 = 'adaad2d2f78d343c2ed28150fde159386230aedb5d1e6290d521a702dc673201'  # upstream SHA256SUMS
RES = 'Light Touch.app/Contents/Resources/'


# --- LZFSE (the 'bvx1'/'bvx2'/'bvx-' block format of Apple's reference lzfse) ---

L_BITS = [0] * 16 + [2, 3, 5, 8]
M_BITS = [0] * 16 + [3, 5, 8, 11]
D_BITS = [0, 0, 0, 0] + [n for n in range(1, 16) for _ in range(4)]


def _bases(bits):
    out, v = [], 0
    for b in bits:
        out.append(v)
        v += 1 << b
    return out


L_BASE, M_BASE, D_BASE = _bases(L_BITS), _bases(M_BITS), _bases(D_BITS)


class Corrupt(Exception):
    pass


def _freq_value(bits):
    """One packed v2 frequency: (value, bit count)."""
    b = bits & 31
    n = (2, 3, 2, 5, 2, 3, 2, 8, 2, 3, 2, 5, 2, 3, 2, 14)[b & 15]
    if n == 8:
        return 8 + ((bits >> 4) & 0xf), n
    if n == 14:
        return 24 + ((bits >> 4) & 0x3ff), n
    return (0, 2, 1, 4, 0, 3, 1, -1, 0, 2, 1, 5, 0, 3, 1, -1,
            0, 2, 1, 6, 0, 3, 1, -1, 0, 2, 1, 7, 0, 3, 1, -1)[b], n


def _table(nstates, freq, bits=None, base=None):
    """FSE decoder table: per state (symbol-or-base, value bits, total bits, delta)."""
    table, total = [], 0
    for sym, f in enumerate(freq):
        if not f:
            continue
        total += f
        if total > nstates:
            raise Corrupt('frequency table overflows')
        k = nstates.bit_length() - f.bit_length()
        j0 = ((2 * nstates) >> k) - f
        vb = bits[sym] if bits else 0
        vbase = base[sym] if base else sym
        for j in range(f):
            if j < j0:
                table.append((vbase, vb, k + vb, ((f + j) << k) - nstates))
            else:
                table.append((vbase, vb, k - 1 + vb, (j - j0) << (k - 1)))
    if len(table) < nstates:
        table += [(0, 0, 0, 0)] * (nstates - len(table))
    return table


class _Bits:
    """The reference decoder's backwards 64-bit FSE input stream."""

    def __init__(self, data, start, end, n):
        self.d, self.start = data, start
        if n:
            if end - 8 < start:
                raise Corrupt('short stream')
            self.pos = end - 8
            self.accum = int.from_bytes(data[self.pos:end], 'little')
            self.nbits = 64 + n
        else:
            if end - 7 < start:
                raise Corrupt('short stream')
            self.pos = end - 7
            self.accum = int.from_bytes(data[self.pos:end], 'little')
            self.nbits = 56
        if not 56 <= self.nbits < 64 or self.accum >> self.nbits:
            raise Corrupt('bad stream start')

    def flush(self):
        n = (63 - self.nbits) & ~7
        if not n:
            return
        p = self.pos - (n >> 3)
        # The last refill may reach before the payload (the reference reads header bytes there);
        # those bits are never consumed by a valid stream.
        incoming = self.d[max(p, self.start):self.pos].rjust(self.pos - p, b'\0')
        if p < self.start - 8:
            raise Corrupt('stream underflow')
        self.accum = (self.accum << n) | int.from_bytes(incoming, 'little')
        self.pos, self.nbits = p, self.nbits + n

    def pull(self, n):
        self.nbits -= n
        v = self.accum >> self.nbits
        self.accum &= (1 << self.nbits) - 1
        return v


def _block_v2(data, p):
    n_raw = struct.unpack_from('<I', data, p + 4)[0]
    f0, f1, f2 = struct.unpack_from('<QQQ', data, p + 8)
    g = lambda v, o, n: (v >> o) & ((1 << n) - 1)
    hdr = dict(n_raw=n_raw, n_literals=g(f0, 0, 20), n_lit_bytes=g(f0, 20, 20), n_matches=g(f0, 40, 20),
               lit_bits=g(f0, 60, 3) - 7, lit_state=[g(f1, 0, 10), g(f1, 10, 10), g(f1, 20, 10), g(f1, 30, 10)],
               n_lmd_bytes=g(f1, 40, 20), lmd_bits=g(f1, 60, 3) - 7,
               l_state=g(f2, 32, 10), m_state=g(f2, 42, 10), d_state=g(f2, 52, 10))
    q, end = p + 32, p + g(f2, 0, 32)
    freq, accum, nbits = [], 0, 0
    for _ in range(20 + 20 + 64 + 256):
        while q < end and nbits + 8 <= 32:
            accum |= data[q] << nbits
            nbits, q = nbits + 8, q + 1
        v, n = _freq_value(accum)
        if n > nbits:
            raise Corrupt('bad frequency table')
        freq.append(v)
        accum, nbits = accum >> n, nbits - n
    if nbits >= 8 or q != end:
        raise Corrupt('bad frequency table')
    return hdr, freq, end


def _block_v1(data, p):
    v = struct.unpack_from('<IIIIIIIi4HiHHH', data, p)
    hdr = dict(n_raw=v[1], n_literals=v[3], n_matches=v[4], n_lit_bytes=v[5], n_lmd_bytes=v[6],
               lit_bits=v[7], lit_state=list(v[8:12]), lmd_bits=v[12], l_state=v[13], m_state=v[14], d_state=v[15])
    q = p + struct.calcsize('<IIIIIIIi4HiHHH')
    freq = list(struct.unpack_from('<360H', data, q))
    return hdr, freq, q + 720


def _decode_block(data, p, h, freq, out):
    lit_t = _table(1024, freq[104:360])
    lit_start = p
    lit_end = p + h['n_lit_bytes']
    bits = _Bits(data, lit_start, lit_end, h['lit_bits'])
    s = h['lit_state']
    lits = bytearray(h['n_literals'])
    pull, flush = bits.pull, bits.flush
    for i in range(0, h['n_literals'], 4):
        flush()
        for j in range(4):
            sym, _, k, delta = lit_t[s[j]]
            s[j] = delta + pull(k)
            lits[i + j] = sym
    l_t = _table(64, freq[0:20], L_BITS, L_BASE)
    m_t = _table(64, freq[20:40], M_BITS, M_BASE)
    d_t = _table(256, freq[40:104], D_BITS, D_BASE)
    lmd_end = lit_end + h['n_lmd_bytes']
    bits = _Bits(data, lit_end, lmd_end, h['lmd_bits'])
    pull, flush = bits.pull, bits.flush
    ls, ms, ds = h['l_state'], h['m_state'], h['d_state']
    lp, d, start = 0, 0, len(out)

    def value(t, state):
        vbase, vb, tb, delta = t[state]
        x = pull(tb)
        return delta + (x >> vb), vbase + (x & ((1 << vb) - 1))

    for _ in range(h['n_matches']):
        flush()
        ls, lv = value(l_t, ls)
        ms, mv = value(m_t, ms)
        ds, dv = value(d_t, ds)
        if dv:
            d = dv
        out += lits[lp:lp + lv]
        lp += lv
        if mv:
            src = len(out) - d
            if src < 0:
                raise Corrupt('match before start')
            if d >= mv:
                out += out[src:src + mv]
            else:
                while mv:
                    chunk = out[src:src + min(d, mv)]
                    out += chunk
                    mv -= len(chunk)
    if len(out) - start != h['n_raw']:
        raise Corrupt('block size mismatch (%d != %d)' % (len(out) - start, h['n_raw']))
    return lmd_end


def lzfse_decode(data):
    out, p = bytearray(), 0
    while True:
        magic = data[p:p + 4]
        if magic == b'bvx$':
            return bytes(out)
        if magic == b'bvx-':
            n = struct.unpack_from('<I', data, p + 4)[0]
            out += data[p + 8:p + 8 + n]
            p += 8 + n
        elif magic in (b'bvx1', b'bvx2'):
            h, freq, q = (_block_v2 if magic == b'bvx2' else _block_v1)(data, p)
            p = _decode_block(data, q, h, freq, out)
        elif magic == b'bvxn':
            raise Corrupt('LZVN blocks are not supported')
        else:
            raise Corrupt('unknown LZFSE block %r at %d' % (magic, p))


# --- Apple Archive ---

def pbze_decode(data):
    """Apple's chunked compressed stream: 'pbze' + big-endian block size, then (raw, stored) sizes."""
    if data[:4] != b'pbze':
        raise Corrupt('not an LZFSE-compressed Apple Archive (%r)' % data[:4])
    out, p = bytearray(), 12
    while p < len(data):
        raw, stored = struct.unpack_from('>QQ', data, p)
        chunk = data[p + 16:p + 16 + stored]
        out += chunk if raw == stored else lzfse_decode(chunk)
        p += 16 + stored
    return bytes(out)


_FIXED = {'1': 1, '2': 2, '4': 4, '8': 8, 'F': 4, 'G': 20, 'H': 32, 'I': 48, 'J': 64, 'S': 8, 'T': 12}


def aa_entries(buf):
    """Yields (type char, path, data, mode) for each AA01/AA00 entry."""
    p = 0
    while p < len(buf):
        if buf[p:p + 4] not in (b'AA01', b'AA00'):
            raise Corrupt('bad archive entry at %d' % p)
        q, end = p + 6, p + struct.unpack_from('<H', buf, p + 4)[0]
        fields, blobs = {}, []
        while q < end:
            key, t = buf[q:q + 3].decode('ascii'), chr(buf[q + 3])
            q += 4
            if t in _FIXED:
                fields[key] = buf[q:q + _FIXED[t]]
                q += _FIXED[t]
            elif t == 'P':
                n = struct.unpack_from('<H', buf, q)[0]
                fields[key] = buf[q + 2:q + 2 + n]
                q += 2 + n
            elif t in 'ABC':
                w = {'A': 2, 'B': 4, 'C': 8}[t]
                blobs.append((key, int.from_bytes(buf[q:q + w], 'little')))
                q += w
            else:
                raise Corrupt('unknown field %s%s' % (key, t))
        data, b = None, end
        for key, n in blobs:
            if key == 'DAT':
                data = buf[b:b + n]
            b += n
        typ = chr(fields['TYP'][0]) if 'TYP' in fields else '?'
        mode = int.from_bytes(fields['MOD'], 'little') if 'MOD' in fields else None
        yield typ, fields.get('PAT', b'').decode('utf-8'), data, mode
        p = b


# --- the upstream app ZIP ---

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest):
    tmp = dest + '.part'
    with urllib.request.urlopen(url) as r, open(tmp, 'wb') as f:
        total, done = int(r.headers.get('Content-Length') or 0), 0
        for chunk in iter(lambda: r.read(1 << 20), b''):
            f.write(chunk)
            done += len(chunk)
            if total:
                print('\r  %3d%%  %d / %d MB' % (100 * done // total, done >> 20, total >> 20), end='', flush=True)
    print()
    os.replace(tmp, dest)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--zip', help='an already downloaded LightTouchMac-universal.zip')
    ap.add_argument('--out', default='LightTouch-assets', help='output folder (default: %(default)s)')
    ap.add_argument('--armv7', action='store_true', help='also write armv7.itpack (iPod touch 4G guest tools)')
    ap.add_argument('--verified-tools', action='store_true',
                    help='also unpack all of guest.aar into OUT/verified-tools with an inventory.json, '
                         'the folder that IPSW preparation (Android "Verified tools", Linux lighttouch-prepare) reads')
    ap.add_argument('--any-zip', action='store_true', help='accept a ZIP that is not the checked upstream release')
    ap.add_argument('--list-aar', metavar='AAR', help='list the entries of a guest.aar and exit')
    a = ap.parse_args()

    if a.list_aar:
        with open(a.list_aar, 'rb') as f:
            for typ, path, data, _ in aa_entries(pbze_decode(f.read())):
                print('%s %10s  %s' % (typ, len(data) if data is not None else '', path))
        return 0

    os.makedirs(a.out, exist_ok=True)
    src = a.zip
    if not src:
        src = os.path.join(a.out, 'LightTouchMac-universal.zip')
        if not os.path.exists(src):
            print('Downloading the upstream LightTouchMac app (about 390 MB):\n  ' + RELEASE)
            download(RELEASE, src)
    print('Checking ' + src)
    digest = sha256_file(src)
    if digest != RELEASE_SHA256 and not a.any_zip:
        print('SHA-256 %s does not match the checked upstream release.\n'
              'Re-download it, or pass --any-zip to use a different upstream build.' % digest, file=sys.stderr)
        return 1

    with zipfile.ZipFile(src) as z:
        names = set(z.namelist())
        for inner, name in (('Device/n72ap-7E18.itbase', 'n72ap-7E18.itbase'), ('Device/bootrom_240_4', 'bootrom.bin')):
            if RES + inner not in names:
                print('The ZIP has no %s; is it the LightTouchMac app?' % inner, file=sys.stderr)
                return 1
            print('Writing ' + name)
            with z.open(RES + inner) as f, open(os.path.join(a.out, name), 'wb') as o:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    o.write(chunk)
        print('Unpacking the guest tools from guest.aar')
        archive = pbze_decode(z.read(RES + 'Guest/guest.aar'))
    wanted = {'guest-tools/armv6.itpack': 'armv6.itpack'}
    if a.armv7:
        wanted['guest-tools/armv7.itpack'] = 'armv7.itpack'
    tools = os.path.join(a.out, 'verified-tools')
    inventory = []
    for typ, path, data, mode in aa_entries(archive):
        if a.verified_tools and path:
            rel = 'release-guest-unpacked/' + path
            dest = os.path.join(tools, *rel.split('/'))
            if '..' in path.split('/') or path.startswith('/'):
                raise Corrupt('unsafe archive path %r' % path)
            if typ == 'D':
                os.makedirs(dest, exist_ok=True)
                inventory.append({'path': rel, 'mode': mode or 0o755, 'type': 'directory'})
            elif typ == 'F':
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as o:
                    o.write(data)
                inventory.append({'path': rel, 'mode': mode or 0o644, 'type': 'file', 'size': len(data),
                                  'sha256': hashlib.sha256(data).hexdigest()})
        if path in wanted:
            print('Writing ' + wanted[path])
            with open(os.path.join(a.out, wanted.pop(path)), 'wb') as o:
                o.write(data)
    if wanted:
        print('guest.aar has no %s' % ', '.join(wanted), file=sys.stderr)
        return 1
    if a.verified_tools:
        inventory.insert(0, {'path': 'release-guest-unpacked', 'mode': 0o755, 'type': 'directory'})
        with open(os.path.join(tools, 'inventory.json'), 'w') as o:
            json.dump({'schema': 1, 'entries': inventory}, o, indent=1)
        print('Wrote the preparation tools folder ' + tools)
    print('\nDone. In LightTouch, choose these files from %s:\n'
          '  1 iOS system   n72ap-7E18.itbase\n  2 Guest tools  armv6.itpack\n  3 Boot file    bootrom.bin'
          % os.path.abspath(a.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
