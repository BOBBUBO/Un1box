# Build-time synthetic HFSX corpus. Contains no Apple firmware or SDK files.
import pathlib, struct, subprocess, tempfile, sys

def fixture(path):
    block, blocks = 4096, 512
    data = bytearray(block * blocks)
    vh = bytearray(512)
    def be16(buf, off, n): struct.pack_into('>H', buf, off, n)
    def be32(buf, off, n): struct.pack_into('>I', buf, off, n)
    be16(vh, 0, 0x4858); be16(vh, 2, 5)
    be32(vh, 4, 1 << 8); be32(vh, 8, 0x31302e30)
    be32(vh, 36, 1); be32(vh, 40, block); be32(vh, 44, blocks)
    be32(vh, 48, blocks - 11); be32(vh, 52, 10); be32(vh, 64, 16)
    def fork(offset, start, count):
        struct.pack_into('>QII', vh, offset, block * count, block, count)
        be32(vh, offset + 16, start); be32(vh, offset + 20, count)
    fork(112, 1, 1); fork(192, 2, 2); fork(272, 4, 4); fork(352, 8, 2)
    data[1024:1536] = vh; data[-1024:-512] = vh
    # Allocation bitmap: first eight blocks and the alternate-header block.
    data[block] = 0xff; data[block + 1] = 0xc0; data[block + (blocks - 1) // 8] |= 1
    def header(start, total, root, leaves, compare):
        node = bytearray(block)
        struct.pack_into('>IIBBHH', node, 0, 0, 0, 1, 0, 3, 0)
        struct.pack_into('>HIIIIHHII', node, 14, 1 if root else 0, root, leaves, root, root, block, 10 if start == 2 else 266 if start == 8 else 516, total, total - (2 if root else 1))
        node[14 + 36] = 0; node[14 + 37] = compare
        be32(node, 14 + 38, 2 if start == 2 else 6)
        node[248] = 0xc0 if root else 0x80
        for index, offset in enumerate([14, 120, 248, block - 8]): be16(node, block - 2 * (index + 1), offset)
        data[start * block:(start + 1) * block] = node
    header(2, 2, 0, 0, 0); header(4, 4, 1, 2, 0xbc); header(8, 2, 0, 0, 0)
    def key(parent, name):
        name = name.encode('utf-16-be')
        return struct.pack('>HIH', 6 + len(name), parent, len(name) // 2) + name
    folder = bytearray(88); be16(folder, 0, 1); be32(folder, 8, 2); be16(folder, 42, 0o40755)
    thread = struct.pack('>HHIH', 3, 0, 1, 4) + 'Test'.encode('utf-16-be')
    records = [key(1, 'Test') + folder, key(2, '') + thread]
    leaf = bytearray(block); struct.pack_into('>IIBBHH', leaf, 0, 0, 0, 255, 1, 2, 0)
    pos = 14
    for i, record in enumerate(records):
        be16(leaf, block - 2 * (i + 1), pos)
        leaf[pos:pos + len(record)] = record; pos += len(record)
    be16(leaf, block - 6, pos)
    data[5 * block:6 * block] = leaf
    path.write_bytes(data)

if __name__ == '__main__':
    runtime = pathlib.Path(sys.argv[1]).resolve()
    tool = runtime / 'firmware-hfs-test'
    # MSYS Python is native Windows, so sys.platform is win32.
    with tempfile.TemporaryDirectory(prefix='ltw-hfs-') as tmp:
        root = pathlib.Path(tmp); image = root / 'synthetic.img'
        fixture(image)
        def run(*args): subprocess.run([str(tool), str(image), *map(str, args)], check=True, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        payload = root / 'payload.bin'; payload.write_bytes(bytes(range(256)) * 80)
        run('mkdir', '/test', '755', '501')
        run('put', '/test/payload', payload, '755', '501')
        copy = root / 'read.bin'; run('get', '/test/payload', copy)
        assert copy.read_bytes() == payload.read_bytes(), 'HFS insertion roundtrip failed'
        run('portable-grow', 4 * 1024 * 1024)
        run('get', '/test/payload', copy)
        assert image.stat().st_size == 4 * 1024 * 1024 and copy.read_bytes() == payload.read_bytes(), 'HFS grow damaged payload'
        with image.open('rb') as f:
            f.seek(1024); first = f.read(512); f.seek(-1024, 2); alternate = f.read(512)
        assert first == alternate and struct.unpack_from('>I', first, 44)[0] == 1024, 'HFS alternate header mismatch'
        # The actual devices use 7–15 GiB volumes: exercise 64-bit offsets,
        # not only the small fixture used for catalog correctness.
        if sys.platform == 'win32':
            import ctypes
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
            k32.CreateFileW.restype = ctypes.c_void_p
            handle = k32.CreateFileW(str(image), 0x40000000, 3, None, 3, 0x80, None)
            if handle == ctypes.c_void_p(-1).value: raise ctypes.WinError(ctypes.get_last_error())
            returned = ctypes.c_ulong()
            k32.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            try:
                if not k32.DeviceIoControl(handle, 0x900c4, None, 0, None, 0, ctypes.byref(returned), None): raise ctypes.WinError(ctypes.get_last_error())
            finally: k32.CloseHandle(handle)
        large = 5 * 1024 * 1024 * 1024
        run('portable-grow', large)
        assert image.stat().st_size == large, 'HFS grow lost high offset bits'
        with image.open('rb') as f:
            f.seek(1024); first = f.read(512); f.seek(-1024, 2); alternate = f.read(512)
        assert first == alternate and struct.unpack_from('>I', first, 44)[0] == large // 4096, 'Large HFS header mismatch'
        # Force catalog growth beyond the first bitmap byte.
        for i in range(80): run('put', f'/test/item-{i:03}', payload, '644', '501')
        run('symlink', '/test/link', '/test/payload')
        run('attr-set', '/test/payload', 'com.test.metadata', 'retained-value')
        destination = root / 'data.img'; fixture(destination)
        subprocess.run([str(tool), str(image), 'copy-tree', '/test', str(destination), '/'], check=True, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run([str(tool), str(destination), 'get', '/payload', str(copy)], check=True, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert copy.read_bytes() == payload.read_bytes(), 'HFS cross-volume data transfer failed'
        link = subprocess.check_output([str(tool), str(destination), 'readlink', '/link'], timeout=30).decode().strip()
        assert link == '/test/payload', 'HFS transfer followed a symlink instead of preserving it'
        attribute = subprocess.check_output([str(tool), str(destination), 'attr-get', '/payload', 'com.test.metadata'], timeout=30)
        assert attribute == b'retained-value', 'HFS transfer dropped extended attributes'
        subprocess.run([str(tool), str(destination), 'get', '/item-079', str(copy)], check=True, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert copy.read_bytes() == payload.read_bytes(), 'HFS overflow-extent catalog growth damaged a file'
        listing = subprocess.check_output([str(tool), str(destination), 'ls', '/'], timeout=30).decode()
        assert 'payload' in listing, 'HFS catalog listing failed'
        subprocess.run([str(tool), str(destination), 'rm', '/payload'], check=True, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert subprocess.run([str(tool), str(destination), 'get', '/payload', str(copy)], timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode != 0, 'HFS deletion failed'
        # HFSX must preserve distinct names that differ only by case.
        upper = root / 'upper'; lower = root / 'lower'
        upper.write_bytes(b'upper case'); lower.write_bytes(b'lower case')
        run('put', '/test/Case', upper, '644', '0')
        run('put', '/test/case', lower, '644', '0')
        run('get', '/test/Case', copy); assert copy.read_bytes() == upper.read_bytes()
        run('get', '/test/case', copy); assert copy.read_bytes() == lower.read_bytes()
        # A known XPwn exit(0) path must return failure instead of terminating.
        broken = root / 'no-free-space.img'; fixture(broken)
        with broken.open('r+b') as f:
            f.seek(1024 + 48); f.write(struct.pack('>I', 0))
        result = subprocess.run([str(tool), str(broken), 'grow', str(7 * 1024**3)],
                                timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert result.returncode == 1, 'XPwn fatal growth escaped guarded failure boundary'
        print('synthetic HFSX, owners, attrs, symlinks, overflow extents, >4GiB offsets and fatal guard passed')
