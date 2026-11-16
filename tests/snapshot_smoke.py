#!/usr/bin/env python3
"""Run isolated snapshot checks: --image IMAGE --generation v1|v2 [--artifacts-dir DIR]."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

LABEL = "snapshot-smoke.run"
FIXTURE_PATH = "/tmp/snapshot_fixture.py"


class SmokeError(Exception):
    pass


def docker(*arguments, timeout=30, check=True):
    result = subprocess.run(["docker", *arguments], text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        # Docker output and container logs can contain environment secrets; do not export them.
        raise SmokeError("docker " + arguments[0] + " failed (exit " + str(result.returncode) + ")")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--generation", choices=("v1", "v2"), required=True)
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()
    if args.image.startswith("-") or any(char.isspace() for char in args.image):
        parser.error("--image must be a Docker image reference")
    fixture = Path(__file__).resolve().with_name("snapshot_fixture.py")
    run_id = uuid.uuid4().hex
    name = "1panel-snapshot-smoke-" + args.generation + "-" + run_id[:12]
    report = {"image": args.image, "generation": args.generation, "container": name,
              "smoke_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
              "isolation": {"network": "none", "host_binds": [], "privileged": False,
                            "storage": "new anonymous volumes", "docker_api": "empty, container-only socket"}}
    container_id = None
    print(json.dumps({"image": args.image, "container": name, "isolation": report["isolation"]}), flush=True)
    try:
        report["image_id"] = docker("image", "inspect", "--format", "{{.Id}}", args.image).stdout.strip()
        created = docker("create", "--name", name, "--label", LABEL + "=" + run_id,
                         "--network", "none", "--cpus", "1", "--memory", "2g", "--pids-limit", "256",
                         "--security-opt", "no-new-privileges=true",
                         "--env", "DOCKER_HOST=unix:///tmp/snapshot-smoke-docker.sock",
                         "--entrypoint", "/usr/bin/python3", args.image,
                         FIXTURE_PATH, "init", "--generation", args.generation)
        container_id = created.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise SmokeError("Docker did not return a container ID")
        mounts = json.loads(docker("inspect", "--format", "{{json .Mounts}}", container_id).stdout)
        if any(mount["Type"] != "volume" or not re.fullmatch(r"[0-9a-f]{64}", mount.get("Name", ""))
               for mount in mounts) or not any(mount["Destination"] == "/opt" for mount in mounts):
            raise SmokeError("smoke container must use only fresh anonymous volumes, including /opt")
        print(json.dumps({"copy_source": str(fixture), "copy_target": name + ":" + FIXTURE_PATH,
                          "file_count": 1, "bytes": fixture.stat().st_size}), flush=True)
        docker("cp", str(fixture), container_id + ":" + FIXTURE_PATH)
        docker("start", container_id)
        checked = docker("exec", container_id, "/usr/bin/python3", FIXTURE_PATH,
                         "probe", "--generation", args.generation, timeout=420, check=False)
        try:
            report["result"] = json.loads(checked.stdout)
        except ValueError:
            raise SmokeError("container probe did not return a structured result") from None
        if checked.returncode or report["result"].get("passed") is not True:
            raise SmokeError("snapshot regression check failed")
    except SmokeError as error:
        report["error"] = str(error)
    except Exception as error:
        report["error"] = "smoke runner failed (" + type(error).__name__ + ")"
    finally:
        if container_id and re.fullmatch(r"[0-9a-f]{64}", container_id):
            try:
                identified = docker("inspect", "--format", "{{json .Config.Labels}}", container_id)
                if json.loads(identified.stdout).get(LABEL) != run_id:
                    raise SmokeError("cleanup refused: container ownership label does not match")
                print(json.dumps({"cleanup_container": container_id, "anonymous_volumes": True}), flush=True)
                docker("rm", "--force", "--volumes", container_id)
                report["cleanup"] = "removed task container and anonymous volumes"
            except Exception as error:
                report["cleanup"] = "failed (" + type(error).__name__ + ")"
                report.setdefault("error", "task container cleanup failed")
    if args.artifacts_dir:
        directory = args.artifacts_dir.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("snapshot-" + args.generation + "-" + run_id + ".json")
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(report, indent=2) + "\n")
        report["report_path"] = str(path)
    print(json.dumps(report, indent=2), flush=True)
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
