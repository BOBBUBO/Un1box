"""Linux host adapter for shared desktop widgets; preserves existing disk formats."""
from pathlib import Path
import sys

from model import FORMAT, VERSION, Profiles, inspect_base, load_profile, save_json, start_command, import_command, validate, display_title
from session import Session, clean_exit
from preparation import Preparation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_services.bridge import Bridge
from app_services.window import AppsWindow


class LinuxHost:
    font_family = "DejaVu Sans"
    inspect_base = staticmethod(inspect_base)
    display_title = staticmethod(display_title)
    validate = staticmethod(validate)
    load_profile = staticmethod(load_profile)
    clean_exit = staticmethod(clean_exit)
    prepare = staticmethod(Preparation)
    create_session = staticmethod(Session)
    open_profiles = staticmethod(Profiles)
    apps_window = staticmethod(AppsWindow)

    @staticmethod
    def export_profile(path, profile):
        save_json(path, dict(profile, format=FORMAT, version=VERSION))

    @staticmethod
    def guest_command(args, profile, recover):
        return start_command(args.frontend, args.engine, profile, recover)

    @staticmethod
    def import_command(args, source, destination, sha256):
        return import_command(args.frontend, source, destination, sha256)

    @staticmethod
    def start_services(args, directory, identity):
        paths = (args.usbmuxd, args.imobile_library, args.plist_library)
        if not all(Path(path).is_file() for path in paths):
            return None, None
        bridge = Bridge(*paths, directory / "devices" / identity / "pairing", directory / "service-logs")
        try:
            return bridge, bridge.start()
        except Exception:
            bridge.close()
            raise
