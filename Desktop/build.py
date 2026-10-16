#!/usr/bin/env python3
"""One desktop native-build/package dispatcher. Default is a plan, never a build.

This is not a universal cross compiler. Run the same entrypoint on each native
host. Recipes retain their own dependency, safety and resource requirements.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]


def host_identity():
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return [sys.platform, machine]


def matrix(source=SOURCE):
    return json.loads((source / "Desktop/targets.json").read_text())


def private_output(path, source):
    value = Path(path).expanduser().resolve()
    if value == source or source in value.parents or value in source.parents:
        raise ValueError("Build outputs/receipts must be outside the source checkout and its ancestors.")
    return value


def make_plan(target, action, output, forwarded, source=SOURCE, host=None):
    source = Path(source).resolve()
    targets = matrix(source)["targets"]
    if target not in targets:
        raise ValueError("Unknown desktop target: " + target)
    entry = targets[target]
    if action not in entry["actions"]:
        raise ValueError(entry.get("blocker", "Action unavailable for this target: " + action))
    output = private_output(output, source)
    recipe = entry["actions"][action]
    script = source / recipe["recipe"]
    if not script.is_file():
        raise ValueError("Recipe is absent from this source revision: " + str(script))
    # These are dispatcher-owned; never let trailing flags change the recorded inputs.
    for token in forwarded:
        if token.split("=", 1)[0] in {"--app", "--output", "--source"}:
            raise ValueError("--app, --source and --output are owned by the desktop dispatcher.")
    fixed = [str(source) if token == "{source}" else token for token in recipe["fixed"]]
    actual_host = host or host_identity()
    return {"schema": 1, "kind": "desktop-native-build-plan", "target": target,
            "action": action, "host": actual_host, "allowed_hosts": entry["hosts"],
            "host_compatible": actual_host in entry["hosts"],
            "source": str(source), "output": str(output), "ui": entry["ui"],
            "shared_guest_controls": entry["shared_guest_controls"],
            "qualification": entry["qualification"], "resource_control": recipe["resource_control"],
            "argv": [sys.executable, str(script)] + fixed + ["--output", str(output)] + list(forwarded),
            "guest_acceptance": False}


def execute(plan, receipt):
    if not plan["host_compatible"]:
        raise ValueError("Native host required: " + str(plan["allowed_hosts"]) + "; current host: " + str(plan["host"]))
    source = Path(plan["source"])
    receipt = private_output(receipt, source)
    output = Path(plan["output"])
    if receipt == output or output in receipt.parents:
        raise ValueError("Receipt must be separate from the artifact/build output.")
    receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive receipt creation prevents destroying earlier evidence.
    fd = os.open(str(receipt), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        result = dict(plan, kind="desktop-native-build-receipt", status="RUNNING", started_ns=time.time_ns())
        try:
            result["source_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
            if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True):
                raise ValueError("Commit source before executing a recorded build.")
            stream.write(json.dumps(result, indent=2) + "\n"); stream.flush()
            code = subprocess.run(plan["argv"], cwd=source, check=False).returncode
            result.update(status="PASS" if code == 0 else "FAIL", exit_code=code)
            output = Path(plan["output"])
            if code == 0 and not output.exists():
                raise ValueError("Recipe exited successfully but the declared output is absent.")
            if code == 0 and output.is_file():
                digest = hashlib.sha256()
                with output.open("rb") as artifact:
                    for block in iter(lambda: artifact.read(1024 * 1024), b""):
                        digest.update(block)
                result.update(artifact_bytes=output.stat().st_size, artifact_sha256=digest.hexdigest())
            # Directory outputs retain the recipe's exact per-file/build receipts.
            result["recipe_receipts_required"] = True
        except BaseException as error:
            result.update(status="FAIL", failure=str(error))
            raise
        finally:
            result["finished_ns"] = time.time_ns()
            stream.seek(0); stream.truncate(); stream.write(json.dumps(result, indent=2) + "\n"); stream.flush()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--target")
    parser.add_argument("--action", choices=["build", "stage", "package", "engine", "bundle"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true", help="run on the matching native host; caller owns resource allocation")
    parser.add_argument("--receipt", type=Path, help="fresh private file required for execution")
    parser.add_argument("recipe_args", nargs=argparse.REMAINDER, help="arguments for the selected recipe after --")
    args = parser.parse_args()
    try:
        if args.matrix:
            if args.execute:
                parser.error("--matrix cannot execute a build")
            print(json.dumps(matrix(), indent=2)); return
        if not args.target or not args.action or args.output is None:
            parser.error("--target, --action and --output are required")
        forwarded = args.recipe_args[1:] if args.recipe_args[:1] == ["--"] else args.recipe_args
        plan = make_plan(args.target, args.action, args.output, forwarded)
        if args.execute:
            if not args.receipt:
                parser.error("--execute requires a fresh private --receipt")
            result = execute(plan, args.receipt)
            print(json.dumps(result, indent=2))
            raise SystemExit(0 if result["status"] == "PASS" else 1)
        print(json.dumps(plan, indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
