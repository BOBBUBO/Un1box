#!/usr/bin/env python3
"""Thin entrypoint using the maintained desktop Tk implementation."""
import argparse
import os
from pathlib import Path
import tkinter as tk

from lighttouch_desktop.launcher import Launcher
from lighttouch_desktop.windows import WindowsHost


def main():
    parser = argparse.ArgumentParser(description="LightTouch shared Windows desktop")
    root = Path(__file__).resolve().parent.parent  # packaged ui/launcher.py
    parser.add_argument("--frontend", type=Path, default=root/"runtime/LightTouchWin.Headless.exe")
    parser.add_argument("--worker", type=Path, default=root/"runtime/lighttouch-windows-worker.exe")
    parser.add_argument("--services-runtime", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", str(Path.home())))/"LightTouch/SharedWindows")
    args = parser.parse_args()
    window = tk.Tk()
    Launcher(window, args, WindowsHost())
    window.mainloop()


if __name__ == "__main__": main()
