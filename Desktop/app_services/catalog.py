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
import ssl
import tempfile
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class CatalogError(ValueError):
    pass


# AppImage OpenSSL may have Debian paths compiled in while its host is Fedora,
# SUSE or another distribution. Trust remains supplied by the host, not bundled
# in the app or fetched from the network.
_HOST_CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/ca-bundle.pem",
    "/etc/ssl/cert.pem",
)
_HOST_CA_DIRS = ("/etc/ssl/certs", "/etc/pki/tls/certs")


def _tls_context():
    """Verified HTTPS using explicit trust settings or the host's CA store."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Explicit overrides define the trust boundary. Do not silently supplement
    # them with a different host bundle, including when an override is broken.
    if "SSL_CERT_FILE" in os.environ or "SSL_CERT_DIR" in os.environ:
        cafile = os.environ.get("SSL_CERT_FILE") or None
        capath = os.environ.get("SSL_CERT_DIR") or None
        try:
            if not cafile and not capath:
                raise ValueError("SSL_CERT_FILE and SSL_CERT_DIR are empty")
            if capath and not os.path.isdir(capath):
                raise ValueError("SSL_CERT_DIR is not a certificate directory: {}".format(capath))
            context.load_verify_locations(cafile=cafile, capath=capath)
        except (OSError, ValueError) as exc:
            raise CatalogError("Could not load the configured TLS certificate authorities "
                               "(SSL_CERT_FILE / SSL_CERT_DIR): {}".format(exc)) from exc
        return context

    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    if context.cert_store_stats()["x509_ca"]:
        return context
    failures = []
    for cafile in _HOST_CA_FILES:
        if not os.path.isfile(cafile):
            continue
        try:
            context.load_verify_locations(cafile=cafile)
            if context.cert_store_stats()["x509_ca"]:
                return context
            failures.append("{} contains no CA certificates".format(cafile))
        except OSError as exc:
            failures.append("{}: {}".format(cafile, exc))
    # OpenSSL loads hashed-directory certificates lazily, so an empty in-memory
    # store does not imply failure when a standard host CA directory is present.
    default_dir = ssl.get_default_verify_paths().capath
    for capath in ((default_dir,) if default_dir else ()) + _HOST_CA_DIRS:
        if os.path.isdir(capath):
            try:
                context.load_verify_locations(capath=capath)
                return context
            except OSError as exc:
                failures.append("{}: {}".format(capath, exc))
    detail = " ({})".format("; ".join(failures)) if failures else ""
    raise CatalogError("No usable system TLS certificate authorities were found. "
                       "Install your distribution's ca-certificates package or set "
                       "SSL_CERT_FILE / SSL_CERT_DIR to its trust store.{}".format(detail))


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


def _runs(archs, target):
    return isinstance(archs, list) and any(a == target.arch or (target.arch == "armv7" and a == "armv6") for a in archs)


def _requires(minimum, target):
    """True when the target's iOS is older than minimum; unreadable minimums never fit."""
    if minimum is None:
        return False
    try:
        return _version(minimum) > _version(target.ios)
    except CatalogError:
        return True


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
    if not _runs(binary.get("architectures"), target):
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
        self._opener = opener if opener is not None else build_opener(
            _HTTPSRedirect(), HTTPSHandler(context=_tls_context()))

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

    def compatible_versions(self, app, target):
        """Every archived copy of the app that could run on the target, in catalog order.

        The copy the search row names is always kept, so the picker can show it.
        Each row is {"ipa_id", "version", "size", "minimum_os"}; choose() rechecks one.
        """
        rows = []
        for entry in self.versions(app):
            if not isinstance(entry, dict) or not isinstance(entry.get("copies"), list):
                continue
            minimum = entry.get("minimum_os_version")
            for copy in entry["copies"]:
                try:
                    ipa_id = int(copy["ipa_id"])
                except (TypeError, KeyError, ValueError):
                    continue
                macho = copy.get("macho_min_os")
                fits = (copy.get("install_status") == "installable" and _runs(copy.get("architectures"), target)
                        and not _requires(minimum, target) and not _requires(macho, target))
                if fits or ipa_id == app["ipa_id"]:
                    known = [m for m in (minimum, macho) if isinstance(m, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", m)]
                    size = copy.get("size")
                    rows.append({"ipa_id": ipa_id, "version": str(entry.get("version") or "Unknown"),
                                 "size": size if type(size) is int and size > 0 else None,
                                 "minimum_os": max(known, key=lambda m: tuple(map(int, m.split(".")))) if known else None})
        return rows

    def choose(self, app, ipa_id, target):
        """Revalidate one archived copy of app against target; returns the row download() takes."""
        found = self.compatible_copy(ipa_id, target)
        if found.get("bundle_id") != app.get("bundle_id"):
            raise CatalogError("This copy belongs to a different app.")
        check_copy(self.copy_details(ipa_id), found, target)
        return found

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
