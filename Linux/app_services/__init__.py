"""Compatibility import for the single shared desktop app-services source.

Source checkouts historically place Linux/ on sys.path. Packaged applications
stage Desktop/app_services directly, so no checkout-relative shim is shipped.
"""
from pathlib import Path

_shared = Path(__file__).resolve().parents[2] / "Desktop" / "app_services"
if not _shared.is_dir():
    raise ImportError("The shared LightTouch desktop app-services package is missing.")
__path__ = [str(_shared)]
