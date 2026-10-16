#!/usr/bin/env python3
"""Acquire one exact catalog input into private storage; never prepare or boot it."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import ssl
import sys
import time
import urllib.request

MAX_BYTES = 335544320
MAX_SECONDS = 300


def write_receipt(path, result):
    temporary = path.with_suffix(path.suffix + ".writing")
    with temporary.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise ValueError("refusing HTTPS redirect to insecure URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verified_https_open(request, context, timeout):
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), HTTPSRedirects()).open(
        request, timeout=timeout)


def acquire(config, opener=verified_https_open):
    expected_size, expected_sha1 = config["bytes"], config["sha1"].lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha1):
        raise ValueError("expected SHA-1 must be an exact catalog digest")
    if not 0 < expected_size <= MAX_BYTES or not 0 < config["seconds"] <= MAX_SECONDS:
        raise ValueError("input exceeds reviewed size/time limits")
    out = Path(config["out"])
    partial, receipt = Path(str(out) + ".partial"), Path(config["receipt"])
    if not out.is_absolute() or not receipt.is_absolute() or out.parent != receipt.parent:
        raise ValueError("output and receipt must share one absolute private directory")
    if out.exists() or partial.exists() or receipt.exists():
        raise ValueError("refusing existing output/partial/receipt")
    out.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent_stat = out.parent.stat()
    if parent_stat.st_uid != os.getuid() or parent_stat.st_mode & 0o077:
        raise ValueError("input directory must be private and owned by this user")
    if out.parent.is_symlink():
        raise ValueError("input directory may not be a symlink")
    start = time.monotonic()
    deadline = start + config["seconds"]
    result = dict(status="RUNNING", out=str(out), partial=str(partial), bytes_expected=expected_size,
                  sha1_expected=expected_sha1, source_code_sha256=config.get("source_code_sha256"),
                  original_monotonic_deadline=deadline, attempts=[], no_prepare_or_guest=True)
    write_receipt(receipt, result)
    try:
        sources = [("existing", p) for p in config.get("existing", [])]
        sources += [("https", u) for u in config["urls"]]
        for index, (kind, source) in enumerate(sources):
            attempt = dict(kind=kind, source=source, started_monotonic=time.monotonic())
            result["attempts"].append(attempt)
            write_receipt(receipt, result)
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("original acquisition deadline reached")
                if kind == "existing":
                    path = Path(source)
                    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected_size:
                        raise ValueError("existing candidate has wrong type/size")
                    stream = path.open("rb")
                else:
                    if not source.startswith("https://"):
                        raise ValueError("catalog download must use HTTPS")
                    stream = opener(urllib.request.Request(source, headers={"User-Agent": "LightTouch-private-firmware-input/1"}),
                                    context=ssl.create_default_context(), timeout=min(30, remaining))
                    if not stream.geturl().startswith("https://"):
                        stream.close()
                        raise ValueError("HTTPS redirected to an insecure URL")
                    length = stream.headers.get("Content-Length")
                    if length is not None and int(length) != expected_size:
                        stream.close()
                        raise ValueError("HTTP Content-Length disagrees with catalog")
                sha1, sha256 = hashlib.sha1(), hashlib.sha256()
                written = 0
                # Each failed attempt keeps its own partial; next attempt uses a distinct filename.
                attempt_partial = partial if index == 0 else Path(str(partial) + "." + str(index))
                attempt["partial"] = str(attempt_partial)
                with stream, attempt_partial.open("xb") as target:
                    os.chmod(attempt_partial, 0o600)
                    while True:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("original acquisition deadline reached")
                        block = stream.read(min(1024 * 1024, expected_size + 1 - written))
                        if not block:
                            break
                        written += len(block)
                        if written > expected_size or written > MAX_BYTES:
                            raise ValueError("download exceeds catalog/reviewed byte limit")
                        target.write(block)
                        sha1.update(block)
                        sha256.update(block)
                    target.flush()
                    os.fsync(target.fileno())
                attempt.update(bytes=written, sha1=sha1.hexdigest(), sha256=sha256.hexdigest())
                if written != expected_size or sha1.hexdigest() != expected_sha1:
                    raise ValueError("source content does not match exact catalog size/SHA-1")
                os.chmod(attempt_partial, 0o400)
                attempt_partial.replace(out)
                attempt["status"] = "VERIFIED"
                result.update(status="VERIFIED_INPUT_ONLY", bytes=written, sha1=sha1.hexdigest(),
                              sha256=sha256.hexdigest(), verified_source=source, seconds=time.monotonic()-start)
                write_receipt(receipt, result)
                return result
            except Exception as exc:
                attempt.update(status="FAILED", error=type(exc).__name__ + ": " + str(exc),
                               ended_monotonic=time.monotonic())
                write_receipt(receipt, result)
                if time.monotonic() >= deadline:
                    break
        raise RuntimeError("no source passed within the original acquisition deadline")
    except BaseException as exc:
        result.update(status="FAILED", error=type(exc).__name__ + ": " + str(exc), seconds=time.monotonic()-start)
        write_receipt(receipt, result)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Private exact catalog input JSON.")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    def expired(signum, frame):
        raise TimeoutError("hard original acquisition deadline reached")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, min(config["seconds"], MAX_SECONDS))
    try:
        print(json.dumps(acquire(config), sort_keys=True))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    main()
