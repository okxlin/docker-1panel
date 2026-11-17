#!/usr/bin/env python3
"""Test V2 source visibility with new task-owned bind data and a local-driver volume."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

LABEL = "backup-smoke.run"
DOCKER_HOST = "unix:///var/run/docker.sock"


class SmokeError(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--data-image", default="busybox:latest", help="Local image with sh/sleep and no declared volumes")
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--copy-helper", action="store_true", help="Probe an older image with a task-local helper copy")
    parser.add_argument("--panel-config", choices=("/etc/supervisor/conf.d/supervisord.conf", "/etc/1panel/supervisord.conf"))
    args = parser.parse_args()
    for image in (args.image, args.data_image):
        if image.startswith("-") or any(char.isspace() for char in image):
            parser.error("images must be Docker image references")
    root = Path(__file__).resolve().parents[1]
    sources = [root / "tests/backup_fixture.py", root / "tests/snapshot_fixture.py"]
    helper = root / "V2/backup-check"
    run_id = uuid.uuid4().hex
    project = "1panel-backup-smoke-" + run_id[:12]
    report = {"image": args.image, "data_image": args.data_image, "run_id": run_id, "passed": False,
              "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources + [helper, Path(__file__)]},
              "isolation": {"panel_network": "none", "privileged": False, "devices": [], "host_socket": False,
                            "host_data": "only new task-owned bind files and named-volume mountpoint, read-only",
                            "docker_metadata": "controlled read-only fixture; existing default network",
                            "restore": "not exercised; original-file and application consistency are outside the helper contract"},
              "copied_helper": args.copy_helper, "scenarios": []}
    started = time.monotonic()
    containers, volume_names = [], []
    with tempfile.TemporaryDirectory(prefix="1panel-backup-smoke-") as directory:
        task_root = Path(directory).resolve()
        config = task_root / "docker-config"
        config.mkdir(mode=0o700)
        env = os.environ.copy()
        env.update(DOCKER_CONFIG=str(config), DOCKER_HOST=DOCKER_HOST)
        for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            env.pop(key, None)

        def docker(*arguments, timeout=30, check=True):
            result = subprocess.run(["docker", *arguments], env=env, text=True, capture_output=True, timeout=timeout)
            if check and result.returncode:
                hint = next((message for message in ("bind source path does not exist", "unexpected key", "invalid mount config",
                            "not a directory", "no such file", "already in use") if message in result.stderr), "operation rejected")
                raise SmokeError("task docker " + arguments[0] + " failed (exit " + str(result.returncode) + "): " + hint)
            return result

        def owned_container(*arguments):
            value = docker("create", "--label", LABEL + "=" + run_id, "--network", "none",
                           "--security-opt", "no-new-privileges=true", "--pids-limit", "256", *arguments).stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise SmokeError("Docker did not return a task container ID")
            containers.append(value)
            return value

        def inspect(identifier):
            return json.loads(docker("inspect", identifier).stdout)[0]

        def owned_volume(name):
            if docker("volume", "inspect", name, check=False).returncode == 0:
                raise SmokeError("refuse to reuse a preexisting volume")
            docker("volume", "create", "--driver", "local", "--label", LABEL + "=" + run_id, name)
            volume_names.append(name)
            value = json.loads(docker("volume", "inspect", name).stdout)[0]
            if value.get("Driver") != "local" or value.get("Labels", {}).get(LABEL) != run_id or \
                    not re.fullmatch(r"/[^\0]*/volumes/" + re.escape(name) + r"/_data", value.get("Mountpoint", "")):
                raise SmokeError("task volume identity/mountpoint differs")
            return value

        def remove_container(identifier):
            labels = json.loads(docker("inspect", "--format", "{{json .Config.Labels}}", identifier).stdout)
            if labels.get(LABEL) != run_id:
                raise SmokeError("cleanup refused: task container label differs")
            print(json.dumps({"cleanup_container": identifier, "anonymous_volumes": True}), flush=True)
            docker("rm", "--force", "--volumes", identifier)
            containers.remove(identifier)

        try:
            daemon = docker("info", "--format", "{{json .ID}}\n{{json .Name}}")
            daemon_id, daemon_name = [json.loads(line) for line in daemon.stdout.splitlines()]
            if not Path("/var/run/docker.sock").is_socket() or not daemon_id or not daemon_name:
                raise SmokeError("local test Docker endpoint is unavailable")
            report["controller"] = {"host": DOCKER_HOST, "id": daemon_id, "name": daemon_name,
                                    "credentials": "empty task-local Docker config"}
            report["image_id"] = docker("image", "inspect", "--format", "{{.Id}}", args.image).stdout.strip()
            report["data_image_id"] = docker("image", "inspect", "--format", "{{.Id}}", args.data_image).stdout.strip()
            data_config = json.loads(docker("image", "inspect", "--format", "{{json .Config}}", args.data_image).stdout)
            if data_config.get("Volumes"):
                raise SmokeError("data image must not create extra implicit volumes")
            # Build bind sources through a new daemon-owned volume, also when the runner is itself containerized.
            source_volume = owned_volume(project + "-sources")
            source_root = Path(source_volume["Mountpoint"])
            bind, empty, compose, single = (source_root / name for name in ("bind", "empty", "project", "single.txt"))
            print(json.dumps({"source_root": str(source_root), "seed_target": "/seed", "files": 3,
                              "bytes": sum(map(len, (b"1Panel task-owned bind bytes\n", b"1Panel task-owned single file bytes\n", b"services: {}\n"))),
                              "bind_targets": [str(bind), str(empty), str(compose), str(single)]}), flush=True)
            seed = owned_container("--mount", "type=volume,src=" + source_volume["Name"] + ",dst=/seed,volume-nocopy",
                                   "--entrypoint", "/bin/sh", args.data_image, "-c",
                                   "mkdir /seed/bind /seed/empty /seed/project; "
                                   "printf '1Panel task-owned bind bytes\n' > /seed/bind/sentinel.txt; "
                                   "printf '1Panel task-owned single file bytes\n' > /seed/single.txt; "
                                   "printf 'services: {}\n' > /seed/project/compose.yml")
            docker("start", "--attach", seed)
            if inspect(seed)["State"]["ExitCode"] != 0:
                raise SmokeError("task bind-source seeding failed")
            remove_container(seed)
            volume_name = project + "-volume"
            volume = owned_volume(volume_name)
            report["task_volume"] = {key: volume[key] for key in ("Name", "Driver", "Mountpoint")}
            print(json.dumps({"same_path_volume_source": volume["Mountpoint"], "target": volume["Mountpoint"],
                              "writes": "seed only the new labelled volume via the task data container"}), flush=True)
            app = owned_container("--name", project + "-app", "--label", "com.docker.compose.project=" + project,
                                  "--label", "com.docker.compose.project.working_dir=" + str(compose),
                                  "--label", "com.docker.compose.project.config_files=" + str(compose / "compose.yml"),
                                  "--mount", "type=bind,src=" + str(bind) + ",dst=/bind-data",
                                  "--mount", "type=bind,src=" + str(empty) + ",dst=/empty-data,readonly",
                                  "--mount", "type=bind,src=" + str(single) + ",dst=/file-data,readonly",
                                  "--mount", "type=volume,src=" + volume_name + ",dst=/volume-data,volume-nocopy",
                                  "--mount", "type=tmpfs,dst=/ephemeral", "--entrypoint", "/bin/sh", args.data_image, "-c",
                                  "printf '1Panel task-owned volume bytes\n' > /volume-data/sentinel.txt; exec sleep 600")
            docker("start", app)
            actual_app = inspect(app)
            report["actual_task_mounts"] = [{"type": mount["Type"], "destination": mount["Destination"]}
                                            for mount in actual_app["Mounts"]]
            persistent = {mount["Destination"]: mount["Type"] for mount in actual_app["Mounts"] if mount["Type"] != "tmpfs"}
            if persistent != {"/bind-data": "bind", "/empty-data": "bind", "/file-data": "bind", "/volume-data": "volume"}:
                raise SmokeError("task data container mounts differ")
            raw = docker("exec", app, "cat", "/volume-data/sentinel.txt").stdout.encode()
            if raw != b"1Panel task-owned volume bytes\n":
                raise SmokeError("task named-volume seed bytes differ")
            app_info = {key: actual_app[key] for key in ("Id", "Name", "Created", "Image", "Mounts", "NetworkSettings")}
            app_info.update(State={"Status": "running", "Running": True},
                            Config={"Image": actual_app["Config"]["Image"], "Env": [], "Labels": actual_app["Config"]["Labels"]},
                            HostConfig={"Binds": actual_app["HostConfig"]["Binds"], "NetworkMode": "none"})
            options = ["--panel-config", args.panel_config] if args.panel_config else []
            for scenario in ("missing", "visible"):
                mounts = []
                if scenario == "visible":
                    for source in (bind, empty, single, compose, Path(volume["Mountpoint"])):
                        mounts.extend(["--mount", "type=bind,src=" + str(source) + ",dst=" + str(source) + ",readonly"])
                panel = owned_container("--name", project + "-" + scenario, "--cpus", "1", "--memory", "2g", *mounts,
                                        "--env", "DOCKER_HOST=unix:///tmp/snapshot-smoke-docker.sock",
                                        "--entrypoint", "/usr/bin/python3", args.image, "/tmp/backup_fixture.py", "init", *options)
                actual_panel = inspect(panel)
                for mount in actual_panel["Mounts"]:
                    if mount["Type"] == "bind":
                        if scenario != "visible" or mount["Source"] != mount["Destination"] or mount["RW"]:
                            raise SmokeError("panel data source mapping differs")
                    elif mount["Type"] != "volume" or not re.fullmatch(r"[0-9a-f]{64}", mount.get("Name", "")):
                        raise SmokeError("unexpected panel mount outside the task boundary")
                panel_info = {"Id": panel, "Name": actual_panel["Name"], "Mounts": actual_panel["Mounts"],
                              "Config": {"Hostname": actual_panel["Config"]["Hostname"], "Env": []}}
                info_path = task_root / (scenario + ".json")
                info_path.write_text(json.dumps({"app": app_info, "panel": panel_info, "project": project,
                                               "source_root": str(source_root),
                                               "volume": {key: volume[key] for key in ("Name", "Driver", "Mountpoint", "Labels", "Options", "Scope")},
                                               "helper_sha256": report["source_sha256"][helper.name]}) + "\n", encoding="utf-8")
                for source in sources:
                    print(json.dumps({"copy_source": str(source), "copy_target": panel + ":/tmp/" + source.name,
                                      "file_count": 1, "bytes": source.stat().st_size}), flush=True)
                    docker("cp", str(source), panel + ":/tmp/" + source.name)
                docker("cp", str(info_path), panel + ":/tmp/backup-smoke-info.json")
                helper_path = "/usr/local/bin/1panel-backup-check"
                if args.copy_helper:
                    helper_path = "/tmp/backup-check"
                    docker("cp", str(helper), panel + ":" + helper_path)
                docker("start", panel)
                checked = docker("exec", panel, "/usr/bin/python3", "/tmp/backup_fixture.py", "probe",
                                 "--scenario", scenario, "--helper", helper_path, *options, timeout=300, check=False)
                lines = checked.stdout.splitlines()
                index = next((i for i, line in enumerate(lines) if line == "{"), None)
                if index is None:
                    raise SmokeError("actual panel backup probe did not emit structured output")
                value = json.loads("\n".join(lines[index:]))
                report["scenarios"].append(value)
                if checked.returncode or value.get("passed") is not True:
                    raise SmokeError("actual panel backup control failed")
                remove_container(panel)
            report["passed"] = True
        except Exception as error:
            report["error"] = str(error) if isinstance(error, SmokeError) else "backup runner failed (" + type(error).__name__ + ")"
        finally:
            for identifier in list(reversed(containers)):
                try:
                    remove_container(identifier)
                except Exception as error:
                    report.update(passed=False, cleanup_error="task container cleanup failed (" + type(error).__name__ + ")")
            for volume_name in list(reversed(volume_names)):
                try:
                    volume = json.loads(docker("volume", "inspect", volume_name).stdout)[0]
                    if volume.get("Labels", {}).get(LABEL) != run_id:
                        raise SmokeError("cleanup refused: named-volume ownership differs")
                    print(json.dumps({"cleanup_named_volume": volume_name, "mountpoint": volume["Mountpoint"]}), flush=True)
                    docker("volume", "rm", volume_name)
                    report["cleanup"] = "removed only task-labelled containers, anonymous volumes and named volume"
                except Exception as error:
                    report.update(passed=False, cleanup_error="task volume cleanup failed (" + type(error).__name__ + ")")
    report["seconds"] = round(time.monotonic() - started, 3)
    if args.artifacts_dir:
        directory = args.artifacts_dir.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("backup-smoke-" + run_id + ".json")
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(report, indent=2) + "\n")
        report["report_path"] = str(path)
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
