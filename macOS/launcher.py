#!/usr/bin/env python3
"""macOS entrypoint for the exact maintained desktop Tk launcher."""
import argparse
import fcntl
from pathlib import Path
import sys

_source = Path(__file__).resolve().parents[1]
_shared = _source / "Desktop"
sys.path.insert(0, str(_shared if _shared.is_dir() else Path(__file__).resolve().parent))
from lighttouch_desktop.launcher import Launcher, tk, messagebox
from lighttouch_desktop.macos import MacHost


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    contents = Path(__file__).resolve().parents[1]
    parser.add_argument("--frontend", default=str(contents / "Helpers/LightTouchDesktopHost"))
    parser.add_argument("--engine", default=str(contents / "Frameworks/libqemu-arm.dylib"))
    parser.add_argument("--helper", default=str(contents / "Helpers/LightTouchDevice"))
    parser.add_argument("--firmwarekit", default=str(contents / "MacOS/firmwarekit"))
    parser.add_argument("--data-dir", default=str(Path.home() / "Library/Application Support/LightTouch Shared Desktop"))
    args = parser.parse_args()
    args.data_dir = str(Path(args.data_dir).expanduser().absolute())
    Path(args.data_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    window = tk.Tk()
    lease = open(Path(args.data_dir) / ".launcher-lease", "a")
    try:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Launcher(window, args, MacHost())
        window.mainloop()
    except (ValueError, OSError, RuntimeError) as error:
        messagebox.showerror("LightTouch", str(error), parent=window)
        window.destroy()
        return 1
    finally:
        lease.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
