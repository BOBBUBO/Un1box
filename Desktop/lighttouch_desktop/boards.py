"""The boards a prepared base boots on, as the portable library's boot_route has them (Portable/src/Storage.cpp).

The S5L8900 (iPod touch 1G, original iPhone) and S5L8720 (iPod touch 2G) start from their SecureROM, a separate
boot ROM file; the S5L8920/S5L8922 (iPhone 3GS, iPod touch 3G) and A4 boards from their prepared files alone.
"""

# board: (device name, SoC family)
BOARDS = {
    "n45ap": ("iPod touch (1st generation)", "s5l8900"),
    "m68ap": ("iPhone", "s5l8900"),
    "n72ap": ("iPod touch (2nd generation)", "s5l8720"),
    "n18ap": ("iPod touch (3rd generation)", "a4"),
    "n88ap": ("iPhone 3GS", "a4"),
    "k48ap": ("iPad", "a4"),
    "n81ap": ("iPod touch (4th generation)", "a4"),
    "n90ap": ("iPhone 4", "a4"),
}

# The boot ROM each SecureROM family starts from, and its SHA-256.
BOOTROMS = {
    "s5l8900": ("S5L8900 SecureROM", "220027d6f8ea24603f8c5ad5e920ab553d85a9cd809d79c7e6f6f0eefdc2bc95"),
    "s5l8720": ("S5L8720 SecureROM (iBoot-240.4)", "55f4d8ea2791ba51dd89934168f38f0fb21ce8762ff614c1e742407c0d3ca054"),
}


def supported(board):
    return board in BOARDS


def device_name(board):
    return BOARDS[board][0] if board in BOARDS else "Prepared device"


def needs_bootrom(board):
    return board in BOARDS and BOARDS[board][1] in BOOTROMS


def bootrom(board):
    """(name, sha256) of the boot ROM a board starts from, or None."""
    return BOOTROMS.get(BOARDS[board][1]) if board in BOARDS else None


def default_strategy(board):
    return "kboot" if BOARDS[board][1] == "a4" else "iboot"


def boot_outputs(board, strategy):
    """The lock outputs (key: file) a board's boot reads; ValueError for a route nothing boots."""
    family = BOARDS[board][1]
    iboot, nor, gid, kboot = ("iboot", "iBoot.bin"), ("nor", "nor.bin"), ("gid_blobs", "gid-blobs.bin"), ("kboot", "kboot.bin")
    routes = {
        ("s5l8900", "iboot"): [iboot, nor],
        ("s5l8720", "iboot"): [iboot, nor, gid],
        ("s5l8720", "bootrom"): [nor, gid],
        ("a4", "kboot"): [kboot],  # and nor.bin when the device has one
    }
    if board == "k48ap" and strategy == "iboot":
        return dict([iboot, nor, gid])
    if (family, strategy) not in routes:
        raise ValueError("This board's prepared boot route is unsupported.")
    return dict(routes[(family, strategy)])
