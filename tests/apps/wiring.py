#!/usr/bin/env python3
"""Checks the app manager's wiring: guest uninstall action -> helper CLI -> Kotlin -> Settings.

No Android toolchain and no device are needed, so this is provable locally before a CI run
(same role as tests/mic/wiring.py for the engine): it reads the sources and asserts the pieces
still line up. It cannot prove behaviour; the helper was exercised against stub libimobiledevice
symbols, and the screen still needs a device.

    python3 tests/apps/wiring.py
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "Android/app/src/main/java/org/lighttouch/android"
HELPER = ROOT / "Android/native-services/lt_appservice.c"

failures = []


def text(path):
    return path.read_text(encoding="utf-8")


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + ((": " + detail) if detail and not condition else ""))
    if not condition:
        failures.append(name)


def body(source, signature):
    """The text of one C function, from its signature to the closing brace in column 0."""
    start = source.find(signature)
    if start < 0:
        return ""
    end = source.find("\n}\n", start)
    return source[start:] if end < 0 else source[start:end + 3]


helper = text(HELPER)
kotlin = {p.name: text(p) for p in APP.glob("*.kt")}

# --- the helper: an uninstall action that calls the guest's installer with the app's id ---
uninstall_c = body(helper, "static int action_uninstall(")
check("helper has action_uninstall", bool(uninstall_c))
check("uninstall opens its own installer connection", "instproxy_open(" in uninstall_c)
check("uninstall calls instproxy_uninstall with the bundle id",
      re.search(r"instproxy_uninstall\(\s*client\s*,\s*bundle_id\s*,", uninstall_c) is not None)
check("uninstall frees the client", "instproxy_client_free(" in uninstall_c)
check("uninstall reports the id it removed", 'uninstalled' in uninstall_c and 'bundle_id' in uninstall_c)
check("uninstall says which app failed", 'failed (device error' in uninstall_c or "failed (" in uninstall_c)
check("uninstall is dispatched", re.search(r"is_uninstall\)\s*\{\s*\n\s*rc = action_uninstall\(", helper) is not None)
check("uninstall is a known action", 'is_uninstall = strcmp(action, "uninstall") == 0' in helper)
check("uninstall requires a bundle id", 'bundle_id' in helper and "requires --bundle-id" in helper)
check("bundle ids are validated", "static int bundle_id_valid(" in helper)
check("usage names uninstall", "uninstall --socket" in helper)

# --- Kotlin: the wrapper, the model, the Settings entry, the screen, the routing ---
guest = kotlin["GuestApps.kt"]
check("GuestApps.uninstall asks the helper for uninstall",
      re.search(r'fun uninstall\(socket: String, bundleId: String\)[^\n]*\n[^\n]*listOf\("uninstall", "--bundle-id", bundleId\)',
                guest) is not None)
check("GuestApps.uninstall is bounded", re.search(r"listOf\(\"uninstall\", \"--bundle-id\", bundleId\), \d+\)", guest) is not None)

store = kotlin["StoreScreen.kt"]
remove = body(store, "fun remove(app: InstalledApp)")
check("StoreModel.remove calls the wrapper", "apps.uninstall(socket, app.bundleId)" in remove)
check("StoreModel.remove drops the app from the list", "installedList.set(" in remove)
check("StoreModel.remove re-reads the guest's list", "installedList.refresh(force = true)" in remove)
check("StoreModel.remove reports a failure per app", "removeError" in remove)
check("StoreModel has one removal at a time", re.search(r"if \(removing != null\) return", remove) is not None)

shell = kotlin["Shell.kt"]
check("Screen has APPS", re.search(r"enum class Screen \{[^}]*\bAPPS\b", shell) is not None)

settings = kotlin["SettingsScreen.kt"]
check("Settings has an apps row", 'tag = "settings_apps"' in settings and "navigate(Screen.APPS)" in settings)

apps_screen = kotlin.get("AppsScreen.kt", "")
check("AppsScreen exists", bool(apps_screen))
check("AppsScreen lists the guest's apps", "installed.list" in apps_screen and "installed.refresh(" in apps_screen)
check("AppsScreen confirms before removing", "AlertDialog(" in apps_screen and "apps_confirm_remove" in apps_screen)
check("AppsScreen shows what each app is", "app.bundleId" in apps_screen and "apps_details" in apps_screen)
check("AppsScreen says why it has no list", "Start the device to see its apps" in apps_screen)

activity = kotlin["MainActivity.kt"]
check("Settings opens Apps", re.search(r"Screen\.APPS -> AppsScreen\(", activity) is not None)
check("Apps goes back to Settings", re.search(r"Screen\.APPS -> AppsScreen\([^)]*\)[^\n]*overlay = Screen\.SETTINGS", activity) is not None)
check("Apps hides the guest surface", re.search(r"guestCovered = overlay in listOf\([^)]*\bScreen\.APPS\b", activity) is not None)
check("Apps is not shown without a running guest",
      re.search(r"Screen\.APPS -> AppsScreen\(store, running = status == \"running\" && muxSocket\.isNotEmpty\(\)\)", activity) is not None)

print()
if failures:
    print("{0} check(s) failed: {1}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("all checks passed")
