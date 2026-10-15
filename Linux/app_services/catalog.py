"""Legacy Store API parity with LightTouchCore's CatalogClient/CatalogCopy.

No app payload is fetched until download() is explicitly called. Downloads are
private files; compatibility is checked again immediately before transfer.
MD5 checks archive integrity, not publisher authenticity or permission to use it.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class CatalogError(ValueError):
    pass


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", value):
        raise CatalogError("The minimum iOS version could not be verified.")
    parts = tuple(map(int, value.split(".")))
    if parts[0] == 0:
        raise CatalogError("The minimum iOS version could not be verified.")
    return parts + (0,) * (3 - len(parts))


@dataclass(frozen=True)
class Target:
    product_type: str
    ios: str
    arch: str
    family: str = "1"

    def __post_init__(self):
        if not re.fullmatch(r"(?:iPod|iPhone|iPad)[0-9]+,[0-9]+", self.product_type):
            raise CatalogError("Invalid device product type.")
        _version(self.ios)
        if self.arch not in ("armv6", "armv7") or self.family not in ("1", "2"):
            raise CatalogError("Unsupported guest architecture or device family.")


def incompatibility(app):
    compat = app.get("compat")
    if not isinstance(compat, dict) or compat.get("compatible") is not False:
        return None
    reasons = compat.get("reasons") or []
    reason = str(reasons[0]) if reasons else ""
    if reason == "armv6_slice_contains_armv7_code" or (reason.startswith("no_") and reason.endswith("_slice")):
        return "Needs a newer processor"
    if reason.startswith("requires_ios_"):
        return "Requires iOS " + reason[len("requires_ios_"):]
    if reason.startswith("capability:!") or reason.endswith("device_family") or reason == "family_not_requested":
        return "Not made for this device"
    if reason.startswith("capability:"):
        return "Needs hardware this device does not have"
    return {"encrypted": "Encrypted — cannot open in LightTouch",
            "unavailable": "Download no longer available",
            "not_analyzed": "Not checked for compatibility yet"}.get(reason, "Not compatible with this device")


def check_copy(copy, app, target):
    if str(copy.get("ipa_id")) != str(app["ipa_id"]) or copy.get("bundle_id") != app.get("bundle_id"):
        raise CatalogError("The archived copy no longer matches this app.")
    if copy.get("available") is not True:
        raise CatalogError("This archived download is no longer available.")
    binary = copy.get("binary")
    if not isinstance(binary, dict):
        raise CatalogError("This copy has not been analyzed for compatibility.")
    if binary.get("install_status") != "installable":
        raise CatalogError("This copy is encrypted or has not been classified as installable.")
    archs = binary.get("architectures")
    if not isinstance(archs, list) or not any(a == target.arch or (target.arch == "armv7" and a == "armv6") for a in archs):
        raise CatalogError("This copy needs a newer processor than this device has.")
    if target.arch == "armv6" and binary.get("armv7_code") is True:
        raise CatalogError("This copy needs a newer processor than this device has.")
    families = binary.get("device_family_macho")
    if families and (not isinstance(families, list) or target.family not in map(str, families)):
        raise CatalogError("This copy is not made for this device.")
    for minimum in (app.get("min_os"), binary.get("macho_min_os")):
        if minimum is not None and _version(minimum) > _version(target.ios):
            raise CatalogError("Requires iOS {}; this device runs iOS {}.".format(minimum, target.ios))


def _https(url):
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username is not None or p.password is not None or p.fragment:
        raise CatalogError("The catalog supplied an invalid HTTPS address.")
    return url


class _HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class CatalogClient:
    BASE = "https://legacystore.app"
    USER_AGENT = "LightTouchLinux/0.1 (+https://github.com/blake-lucas/LightTouch)"
    JSON_LIMIT = 8 << 20
    DOWNLOAD_LIMIT = 2 << 30

    def __init__(self, *, opener=None):
        # Injection is for offline tests; normal operation always verifies TLS.
        self._opener = opener or build_opener(_HTTPSRedirect())

    def _open(self, url):
        _https(url)
        try:
            response = self._opener.open(Request(url, headers={"User-Agent": self.USER_AGENT}), timeout=30)
        except HTTPError as exc:
            body = exc.read(4096)
            exc.close()
            if exc.code == 400 and b"device must be one of" in body:
                raise CatalogError("Legacy Store does not support this device yet.") from exc
            message = "The Internet Archive is busy; try again later." if exc.code == 503 else "Legacy Store could not answer the request (HTTP {}).".format(exc.code)
            raise CatalogError(message) from exc
        except (URLError, OSError) as exc:
            raise CatalogError("Could not connect to Legacy Store: {}".format(exc)) from exc
        try:
            _https(response.geturl())
            if response.status != 200:
                raise CatalogError("The catalog returned HTTP {}.".format(response.status))
        except Exception:
            response.close()
            raise
        return response

    def _json(self, path, query=None):
        url = self.BASE + path + ("?" + urlencode(query) if query else "")
        with self._open(url) as response:
            body = response.read(self.JSON_LIMIT + 1)
        if len(body) > self.JSON_LIMIT:
            raise CatalogError("The catalog response exceeds the size limit.")
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise CatalogError("Legacy Store sent an unreadable response.") from exc
        if not isinstance(value, dict):
            raise CatalogError("Legacy Store sent an unreadable response.")
        return value

    def _apps(self, query, target):
        query.update(device=target.product_type, os=target.ios)
        apps = self._json("/api/emulator/apps", query).get("apps")
        if not isinstance(apps, list) or len(apps) > 500:
            raise CatalogError("Legacy Store sent an unreadable app list.")
        for app in apps:
            if (not isinstance(app, dict) or type(app.get("ipa_id")) is not int or app["ipa_id"] <= 0
                    or not isinstance(app.get("name"), str) or not isinstance(app.get("download_url"), str)):
                raise CatalogError("Legacy Store sent an unreadable app entry.")
            compat = app.get("compat")
            if compat is not None and (not isinstance(compat, dict)
                    or type(compat.get("compatible")) is not bool
                    or not isinstance(compat.get("reasons"), list)
                    or any(not isinstance(reason, str) for reason in compat["reasons"])):
                raise CatalogError("Legacy Store sent unreadable compatibility information.")
        return apps

    def search(self, query, target, *, show_unavailable=True):
        params = {"limit": "50"}
        if query:
            params.update(q=query, incompatible="include")
        apps = self._apps(params, target)
        return apps if show_unavailable else [a for a in apps if incompatibility(a) is None]

    def compatible_copy(self, ipa_id, target):
        if type(ipa_id) is not int or ipa_id <= 0:
            raise CatalogError("Invalid archived copy identifier.")
        apps = self._apps({"ipa_id": str(ipa_id)}, target)
        if len(apps) != 1 or apps[0]["ipa_id"] != ipa_id:
            raise CatalogError("This copy is no longer available.")
        if incompatibility(apps[0]):
            raise CatalogError(incompatibility(apps[0]))
        return apps[0]

    def copy_details(self, ipa_id):
        if type(ipa_id) is not int or ipa_id <= 0:
            raise CatalogError("Invalid archived copy identifier.")
        copy = self._json("/api/v1/copies/{}".format(ipa_id))
        if str(copy.get("ipa_id")) != str(ipa_id):
            raise CatalogError("Legacy Store returned a different archived copy.")
        return copy

    def versions(self, app):
        key = app.get("bundle_id") or urlsplit(app.get("app_url") or "").path.rstrip("/").split("/")[-1]
        if not key:
            raise CatalogError("This app has no catalog identifier.")
        versions = self._json("/api/v1/apps/{}/versions".format(quote(key, safe=""))).get("data")
        if not isinstance(versions, list):
            raise CatalogError("Legacy Store sent unreadable version information.")
        return versions

    def download(self, app, target, scratch, *, progress=lambda count, total: None, cancelled=lambda: False):
        deadline = time.monotonic() + 900
        def admit():
            if cancelled():
                raise CatalogError("Download cancelled.")
            if time.monotonic() >= deadline:
                raise CatalogError("Download timed out; try again later.")
        admit()
        current = self.compatible_copy(app["ipa_id"], target)
        if current.get("bundle_id") != app.get("bundle_id"):
            raise CatalogError("The archived copy no longer matches this app.")
        details = self.copy_details(current["ipa_id"])
        check_copy(details, current, target)
        admit()
        checksum = details.get("md5")
        if checksum is not None and (not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", checksum)):
            raise CatalogError("The archive supplied an invalid file checksum.")
        size = details.get("size")
        if size is not None and (type(size) is not int or not 0 < size <= self.DOWNLOAD_LIMIT):
            raise CatalogError("The archive supplied an invalid file size.")
        scratch = Path(scratch)
        scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Each transfer owns a private directory, never an existing IPA path.
        directory = Path(tempfile.mkdtemp(prefix="catalog-{}-".format(current["ipa_id"]), dir=scratch))
        partial, final = directory / "download.partial", directory / "App.ipa"
        try:
            count, digest = 0, hashlib.md5()
            with self._open(current["download_url"]) as response, partial.open("xb") as output:
                os.chmod(partial, 0o600)
                while True:
                    admit()
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > self.DOWNLOAD_LIMIT or (size is not None and count > size):
                        raise CatalogError("The download exceeds its expected size.")
                    output.write(chunk)
                    digest.update(chunk)
                    progress(count, size)
                output.flush()
                os.fsync(output.fileno())
            if count == 0 or (size is not None and count != size):
                raise CatalogError("The download is incomplete or differs from the archive size.")
            if checksum is not None and digest.hexdigest() != checksum.lower():
                raise CatalogError("The downloaded IPA failed its checksum check.")
            admit()
            partial.replace(final)
            return final
        except BaseException:
            partial.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            directory.rmdir()
            raise
