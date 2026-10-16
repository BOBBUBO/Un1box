#!/usr/bin/env python3
"""Linux entrypoint for the maintained shared desktop Tk launcher."""
import argparse
import fcntl
import os
from pathlib import Path
import sys

# Keep source checkout and relocated AppImage import paths compatible.
from model import SetupError
from host import LinuxHost
from lighttouch_desktop.launcher import Launcher as SharedLauncher, ScrollPanel, PALETTE
from lighttouch_desktop.launcher import tk, ttk, filedialog, messagebox


class Launcher(SharedLauncher):
    def __init__(self, window, args, host=None):
        super().__init__(window, args, host or LinuxHost())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    bundle = Path(__file__).resolve().parents[2]
    parser.add_argument("--frontend", default=str(bundle / "bin" / "lighttouch-linux"))
    parser.add_argument("--engine", default=str(bundle / "lib" / "lighttouch" / "libqemu-arm.so"))
    parser.add_argument("--usbmuxd", default=str(bundle / "bin" / "usbmuxd-qemu"))
    parser.add_argument("--imobile-library", default=str(bundle / "lib" / "lighttouch" / "libimobiledevice-1.0.so.6"))
    parser.add_argument("--plist-library", default=str(bundle / "lib" / "lighttouch" / "libplist-2.0.so.4"))
    parser.add_argument("--data-dir", default=str(Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "lighttouch"))
    args = parser.parse_args()
    args.data_dir = str(Path(args.data_dir).expanduser().absolute())
    Path(args.data_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = open(Path(args.data_dir) / ".launcher-lease", "a")
    window = tk.Tk()
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        messagebox.showerror("LightTouch already open", "Use the existing LightTouch launcher for this library.", parent=window)
        window.destroy()
        return 1
    try:
        Launcher(window, args)
        window.mainloop()
    except (SetupError, OSError) as error:
        messagebox.showerror("LightTouch", str(error), parent=window)
        window.destroy()
        return 1
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
