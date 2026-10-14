#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Package original selected AAR/JAR notices; never infer a license grant."""
import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET
import zipfile

MAX_NOTICE = 4 * 1024 * 1024
MAX_NESTED_JAR = 96 * 1024 * 1024
NOTICE_NAME = re.compile(r"(^|/)(licen[cs]es?|notices?|copying|copyrights?|authors|third[-_]?party[-_]?notices?)([^/]*$|/)", re.I)
SAFE_PART = re.compile(r"[A-Za-z0-9._-]+")
HASH = re.compile(r"[0-9a-f]{64}")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(document):
    return (json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True) + "\n").encode()


def https(url):
    p = urlsplit(url)
    if not url.startswith("https://") or p.scheme != "https" or not p.netloc or p.username or p.password:
        raise ValueError("Expected HTTPS provenance URL without credentials")
    return url


def coordinate(value):
    parts = value.split(":")
    if len(parts) != 3 or any(not SAFE_PART.fullmatch(p) or p in (".", "..") for p in parts):
        raise ValueError("Invalid Maven coordinate")
    return parts


def artifact_base(parts):
    group, module, version = parts
    repository = "https://dl.google.com/dl/android/maven2/" if group.startswith("androidx.") else "https://repo.maven.apache.org/maven2/"
    return repository + group.replace(".", "/") + "/" + module + "/" + version + "/"


def archive_notices(data, prefix="", depth=0):
    """Read members without extracting their original paths to the filesystem."""
    found, native = [], []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 50000 or len({i.filename for i in entries}) != len(entries):
            raise ValueError("Too many or duplicate ZIP entries")
        for member in sorted(entries, key=lambda i: i.filename):
            if member.is_dir():
                continue
            name = prefix + member.filename
            is_jar = member.filename.lower().endswith(".jar")
            is_notice = NOTICE_NAME.search(member.filename) is not None and not member.filename.lower().endswith((".class", ".so", ".dex", ".jar"))
            if is_notice or is_jar:
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError("Symlink notice or nested archive")
                maximum = MAX_NESTED_JAR if is_jar else MAX_NOTICE
                if member.file_size > maximum:
                    raise ValueError("Notice or nested archive exceeds bound")
                value = archive.read(member)
                if is_notice:
                    found.append((name, value))
                if is_jar:
                    if depth >= 3:
                        raise ValueError("Nested JAR depth exceeds bound")
                    child, libs = archive_notices(value, name + "!/", depth + 1)
                    found.extend(child)
                    native.extend(libs)
            if member.filename.lower().endswith(".so"):
                native.append({"archive_entry": name, "sha256": sha(archive.read(member)), "size": member.file_size})
    return found, native


def metadata(cache, parts, artifact):
    """Retain exact selected-version metadata, never the whole download cache."""
    root = cache.joinpath(*parts)
    base = artifact_base(parts)
    result = {"artifact_source_url": base + artifact["file"], "artifact_url_basis": "coordinate-derived repository locator"}
    payloads = []
    for suffix in ("pom", "module"):
        candidates = sorted(root.glob("*/*." + suffix))
        candidates = [p for p in candidates if p.name == parts[1] + "-" + parts[2] + "." + suffix]
        values = {sha(p.read_bytes()): p.read_bytes() for p in candidates}
        if len(values) > 1:
            raise ValueError("Conflicting cached metadata for " + ":".join(parts))
        if not values:
            continue
        digest, data = next(iter(values.items()))
        result[suffix + "_sha256"] = digest
        result[suffix + "_source_url"] = base + parts[1] + "-" + parts[2] + "." + suffix
        if suffix == "pom":
            xml = ET.fromstring(data)
            ns = "{http://maven.apache.org/POM/4.0.0}"
            resolved = [xml.findtext(ns + field) or xml.findtext(ns + "parent/" + ns + field) or ""
                        for field in ("groupId", "artifactId", "version")]
            if resolved != parts:
                raise ValueError("POM coordinate does not match selected artifact: " + ":".join(parts))
            result["declared_licenses"] = [{"name": node.findtext(ns + "name") or "", "url": node.findtext(ns + "url") or ""}
                                           for node in xml.findall(ns + "licenses/" + ns + "license")]
            result["upstream_project_url"] = xml.findtext(ns + "url") or ""
            result["upstream_scm_url"] = xml.findtext(ns + "scm/" + ns + "url") or ""
            result["upstream_organization"] = xml.findtext(ns + "organization/" + ns + "name") or ""
            payloads.append(("upstream.pom", data, result["pom_source_url"]))
            if not result["declared_licenses"] and xml.find(ns + "parent") is not None:
                parent = [xml.findtext(ns + "parent/" + ns + name) or "" for name in ("groupId", "artifactId", "version")]
                coordinate(":".join(parent))
                parents = sorted(cache.joinpath(*parent).glob("*/" + parent[1] + "-" + parent[2] + ".pom"))
                parent_values = {sha(p.read_bytes()): p.read_bytes() for p in parents}
                if len(parent_values) > 1:
                    raise ValueError("Conflicting parent POM metadata")
                if parent_values:
                    parent_digest, parent_bytes = next(iter(parent_values.items()))
                    parent_xml = ET.fromstring(parent_bytes)
                    parent_coordinate = [parent_xml.findtext(ns + field) or parent_xml.findtext(ns + "parent/" + ns + field) or ""
                                         for field in ("groupId", "artifactId", "version")]
                    if parent_coordinate != parent:
                        raise ValueError("Parent POM coordinate mismatch")
                    declared = [{"name": n.findtext(ns + "name") or "", "url": n.findtext(ns + "url") or ""}
                                for n in parent_xml.findall(ns + "licenses/" + ns + "license")]
                    parent_url = artifact_base(parent) + parent[1] + "-" + parent[2] + ".pom"
                    result["parent_pom"] = {"coordinate": ":".join(parent), "sha256": parent_digest,
                                             "source_url": parent_url, "declared_licenses": declared}
                    payloads.append(("upstream-parent.pom", parent_bytes, parent_url))
        else:
            model = json.loads(data)
            parent = [model["component"][name] for name in ("group", "module", "version")]
            redirected = parts[1] in (parent[1] + "-android", parent[1] + "-jvm") and model["component"].get("url")
            if parent != parts and not (parent[0] == parts[0] and parent[2] == parts[2] and redirected):
                raise ValueError("Module coordinate does not match selected artifact: " + ":".join(parts))
            result["module_component"] = model["component"]
            urls = {row["url"] for variant in model.get("variants", []) for row in variant.get("files", [])
                    if row.get("name") == artifact["file"] and row.get("sha256") == artifact["sha256"]}
            if len(urls) > 1:
                raise ValueError("Ambiguous upstream module artifact URL")
            if urls:
                result["artifact_source_url"] = https(urljoin(base, next(iter(urls))))
                result["artifact_url_basis"] = "original module metadata name/hash match"
    return result, payloads


def package(inventory, destination, cache, source_reference, source_revision, supplements=None, allow_missing=False):
    https(source_reference)
    if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
        raise ValueError("Source revision must be a full lowercase Git SHA1")
    document = json.loads(inventory.read_text())
    if document.get("schema") != 1 or document.get("configuration") not in ("debugRuntimeClasspath", "releaseRuntimeClasspath"):
        raise ValueError("Expected selected main APK runtime inventory schema 1")
    if not isinstance(document.get("artifacts"), list) or not document["artifacts"]:
        raise ValueError("Selected runtime artifact list must be nonempty")
    rows, occurrences = {}, Counter()
    for row in document["artifacts"]:
        coordinate(row["coordinate"])
        if not HASH.fullmatch(row["sha256"]) or not SAFE_PART.fullmatch(row["file"]) or row["file"] in (".", ".."):
            raise ValueError("Invalid selected artifact filename/hash")
        key = (row["coordinate"], row["file"], row["sha256"])
        if key in rows and Path(row["artifact_path"]).read_bytes() != Path(rows[key]["artifact_path"]).read_bytes():
            raise ValueError("Conflicting duplicate selected artifact")
        rows[key] = row
        occurrences[key] += 1
    supplemental = {}
    if supplements:
        supplied = json.loads(supplements.read_text())
        if supplied.get("schema") != 1:
            raise ValueError("Expected supplemental notice schema 1")
        for entry in supplied["components"]:
            key = (entry["coordinate"], entry["artifact_sha256"])
            if key in supplemental or not any(k[0] == key[0] and k[2] == key[1] for k in rows):
                raise ValueError("Duplicate or unselected supplemental component")
            supplemental[key] = entry
    outputs, components, missing = {}, [], []
    for key, row in sorted(rows.items()):
        parts = coordinate(row["coordinate"])
        data = Path(row["artifact_path"]).read_bytes()
        if sha(data) != row["sha256"]:
            raise ValueError("Selected artifact checksum mismatch: " + row["coordinate"])
        notices, native = archive_notices(data)
        component = {"coordinate": row["coordinate"], "version": parts[2], "artifact_file": row["file"],
                     "artifact_sha256": row["sha256"], "selection_occurrences": occurrences[key],
                     "embedded_notice_count": len(notices), "archive_native_entries": native, "notices": []}
        meta, poms = metadata(cache, parts, row)
        component.update(meta)
        entries = [(name, value, {"origin": "original artifact member", "archive_entry": name,
                                 "source_url": meta["artifact_source_url"]}) for name, value in notices]
        entries += [(name, value, {"origin": "original publisher POM metadata", "source_url": url}) for name, value, url in poms]
        supplement = supplemental.get((row["coordinate"], row["sha256"]), {})
        supplied_notices = supplement.get("notices", [])
        for item in supplied_notices:
            if not SAFE_PART.fullmatch(item["name"]) or item["name"] in (".", "..") or item.get("role") not in ("license", "notice", "attribution"):
                raise ValueError("Invalid supplemental notice name/role")
            if not isinstance(item["upstream_reference"], str) or not item["upstream_reference"].strip():
                raise ValueError("Supplemental upstream version/reference is required")
            value = Path(item["notice_path"]).read_bytes()
            if len(value) > MAX_NOTICE or not HASH.fullmatch(item["sha256"]) or sha(value) != item["sha256"]:
                raise ValueError("Supplemental notice checksum/bound mismatch")
            entries.append((item["name"], value, {"origin": "explicit upstream supplement", "source_url": https(item["source_url"]),
                                                "upstream_reference": item["upstream_reference"], "role": item["role"]}))
        if not notices and not any(i["role"] == "license" for i in supplied_notices):
            missing.append(row["coordinate"])
        folder = "notices/" + "-".join(parts) + "-" + row["sha256"][:12]
        for number, (original, value, origin) in enumerate(entries):
            leaf = re.sub(r"[^A-Za-z0-9._-]", "_", original.rsplit("/", 1)[-1]) or "notice.txt"
            name = folder + "/" + str(number).zfill(3) + "-" + leaf
            if name in outputs:
                raise ValueError("Output notice path collision")
            outputs[name] = value
            component["notices"].append({"path": name, "sha256": sha(value), **origin})
        components.append(component)
    if missing and not allow_missing:
        raise ValueError("Missing primary license/notice bytes for selected inputs: " + ", ".join(missing))
    graph = [{"coordinate": k[0], "file": k[1], "sha256": k[2], "selection_occurrences": occurrences[k]} for k in sorted(rows)]
    manifest = {"schema": 1, "source_reference": source_reference, "source_revision": source_revision,
                "runtime_configuration": document["configuration"], "selected_artifact_rows": sum(occurrences.values()),
                "selected_unique_artifacts": len(rows), "selected_graph_sha256": sha(encoded(graph)), "components": components,
                "missing_primary_notice_components": missing, "notice_byte_coverage": "INCOMPLETE" if missing else "ALL_SELECTED_INPUTS_REPRESENTED",
                "scope": "selected main APK inputs and checked notice bytes; not distribution or complete source/relink clearance",
                "files": [{"path": name, "sha256": sha(outputs[name])} for name in sorted(outputs)]}
    outputs["manifest.json"] = encoded(manifest)
    destination = destination.absolute()
    if destination.is_symlink():
        raise ValueError("Notice destination is a symlink")
    if destination.exists():
        actual = {}
        for path in destination.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlink in notice destination")
            if path.is_file():
                actual[path.relative_to(destination).as_posix()] = path.read_bytes()
        if actual != outputs:
            raise ValueError("Existing notice destination differs; select fresh generated assets")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".frontend-notices-", dir=destination.parent))
        try:
            for name, value in sorted(outputs.items()):
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
                path.chmod(0o644)
            for path in stage.rglob("*"):
                if path.is_dir():
                    path.chmod(0o755)
            stage.chmod(0o755)
            os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return {"schema": 1, "manifest_sha256": sha(outputs["manifest.json"]), "selected_unique_artifacts": len(rows),
            "files": len(outputs), "notice_byte_coverage": manifest["notice_byte_coverage"], "status": "OK"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--metadata-cache", type=Path, required=True)
    parser.add_argument("--source-reference", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--supplements", type=Path)
    parser.add_argument("--allow-missing", action="store_true", help="audit partial extraction; report missing original notice material explicitly")
    args = parser.parse_args()
    try:
        result = package(args.inventory, args.destination, args.metadata_cache, args.source_reference, args.source_revision,
                         args.supplements, args.allow_missing)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as error:
        parser.exit(2, "frontend notices: " + str(error) + "\n")
    print(json.dumps(result, sort_keys=True))
