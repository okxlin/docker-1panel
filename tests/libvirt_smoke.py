#!/usr/bin/env python3
"""Test a V2 client against isolated real libvirt/TCG, without host devices or mounts."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from snapshot_smoke import docker, SmokeError

LABEL = "libvirt-smoke.run"
FIXTURE_PATH = "/tmp/libvirt_fixture.py"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--fixture-image", required=True)
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:+-]*", value)
           for value in (args.image, args.fixture_image)):
        parser.error("image arguments must be Docker image references")
    run_id = uuid.uuid4().hex
    root = Path(__file__).resolve().parent
    report = {"image": args.image, "fixture_image": args.fixture_image,
              "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                for name in ("libvirt_smoke.py", "libvirt_fixture.py", "snapshot_fixture.py")},
              "isolation": {"host_binds": [], "devices": [], "privileged": False,
                            "network": "private fixture namespace", "storage": "new anonymous volumes"}}
    containers = []
    volumes = set()
    print(json.dumps(report), flush=True)
    try:
        report["image_id"] = docker("image", "inspect", "--format", "{{.Id}}", args.image).stdout.strip()
        report["fixture_image_id"] = docker("image", "inspect", "--format", "{{.Id}}", args.fixture_image).stdout.strip()
        fixture = docker("create", "--name", "libvirt-smoke-daemon-" + run_id[:12],
                         "--label", LABEL + "=" + run_id, "--init", "--network", "none",
                         "--cpus", "1", "--memory", "2g", "--pids-limit", "256",
                         "--security-opt", "no-new-privileges=true", args.fixture_image).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", fixture):
            raise SmokeError("Docker did not return a fixture container ID")
        containers.append(fixture)
        inspected = json.loads(docker("inspect", fixture).stdout)[0]
        mounts = inspected["Mounts"]
        if any(m["Type"] != "volume" or not re.fullmatch(r"[0-9a-f]{64}", m.get("Name", "")) for m in mounts):
            raise SmokeError("fixture must use only fresh anonymous volumes")
        volumes.update(m["Name"] for m in mounts)
        shared = {m["Destination"]: m["Name"] for m in mounts}
        if not {"/run/libvirt", "/var/lib/libvirt/images", "/var/lib/libvirt/boot"}.issubset(shared):
            raise SmokeError("fixture is missing its shared volumes")
        docker("start", fixture)
        for _ in range(30):
            ready = docker("exec", fixture, "virsh", "-c", "qemu:///system", "list", "--all", check=False)
            if not ready.returncode:
                break
            time.sleep(1)
        else:
            raise SmokeError("real libvirt fixture did not become available")
        seeded = docker("exec", fixture, "/usr/bin/python3", FIXTURE_PATH, "seed", timeout=90)
        report["fixture"] = json.loads(seeded.stdout)
        client_args = ["create", "--name", "libvirt-smoke-client-" + run_id[:12],
                       "--label", LABEL + "=" + run_id, "--network", "container:" + fixture,
                       "--cpus", "1", "--memory", "2g", "--pids-limit", "256",
                       "--security-opt", "no-new-privileges=true"]
        for target in ("/run/libvirt", "/var/lib/libvirt/images", "/var/lib/libvirt/boot"):
            spec = "type=volume,source=" + shared[target] + ",target=" + target
            client_args.extend(["--mount", spec + (",readonly" if target == "/run/libvirt" else "")])
        client_args.extend(["--env", "DOCKER_HOST=unix:///tmp/snapshot-smoke-docker.sock", "--entrypoint",
                            "/usr/bin/python3", args.image, "/tmp/snapshot_fixture.py", "init", "--generation", "v2"])
        client = docker(*client_args).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", client):
            raise SmokeError("Docker did not return a client container ID")
        containers.append(client)
        mounts = json.loads(docker("inspect", "--format", "{{json .Mounts}}", client).stdout)
        if any(m["Type"] != "volume" or not re.fullmatch(r"[0-9a-f]{64}", m.get("Name", "")) for m in mounts):
            raise SmokeError("client must have only task volumes")
        volumes.update(m["Name"] for m in mounts)
        for name in ("snapshot_fixture.py", "libvirt_fixture.py"):
            source = root / name
            print(json.dumps({"copy_source": str(source), "copy_target": client + ":/tmp/" + name,
                              "files": 1, "bytes": source.stat().st_size}), flush=True)
            docker("cp", str(source), client + ":/tmp/" + name)
        docker("start", client)
        checked = docker("exec", client, "/usr/bin/python3", FIXTURE_PATH, "probe", timeout=180, check=False)
        report["result"] = json.loads(checked.stdout)
        if checked.returncode or report["result"].get("passed") is not True:
            raise SmokeError("real libvirt client regression failed")
        # Independent daemon-side checks prove the RPC mutation and shared file path.
        state = docker("exec", fixture, "virsh", "-c", "qemu:///system", "domstate", "libvirt-smoke").stdout.strip()
        pool = ET.fromstring(docker("exec", fixture, "virsh", "-c", "qemu:///system",
                                   "pool-dumpxml", "smoke-pool").stdout)
        disk = json.loads(docker("exec", fixture, "qemu-img", "info", "--output=json",
                                 "/var/lib/libvirt/images/smoke-pool/client.qcow2").stdout)
        if state != "running" or pool.findtext("target/path") != "/var/lib/libvirt/images/smoke-pool" or \
                disk.get("format") != "qcow2" or disk.get("virtual-size") != 16 * 1024 * 1024:
            raise SmokeError("independent daemon checks did not confirm client operations")
        report["daemon_verified"] = {"vm_state": state, "pool_path": pool.findtext("target/path"),
                                     "client_disk_virtual_bytes": disk["virtual-size"]}
    except Exception as error:
        report["error"] = "libvirt smoke failed (" + type(error).__name__ + ")"
    finally:
        for container in reversed(containers):
            try:
                identified = json.loads(docker("inspect", "--format", "{{json .Config.Labels}}", container).stdout)
                if identified.get(LABEL) != run_id:
                    raise SmokeError("cleanup ownership mismatch")
                print(json.dumps({"cleanup_container": container, "anonymous_volumes": True}), flush=True)
                docker("rm", "--force", "--volumes", container)
            except Exception as error:
                report["error"] = "cleanup failed (" + type(error).__name__ + ")"
        for volume in volumes:
            if docker("volume", "inspect", volume, check=False).returncode == 0:
                report["error"] = "task volume remains after cleanup"
        report["cleanup"] = "removed task containers and anonymous volumes" if "error" not in report else "check report error"
    if args.artifacts_dir:
        directory = args.artifacts_dir.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("libvirt-" + run_id + ".json")
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(report, indent=2) + "\n")
        report["report_path"] = str(path)
    print(json.dumps(report, indent=2))
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
