#!/usr/bin/env python3
"""Check V2 business Supervisor isolation in a fresh, container-only fixture."""

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

LABEL = "supervisor-smoke.run"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--panel-config", default="/etc/1panel/supervisord.conf",
                        choices=("/etc/1panel/supervisord.conf", "/etc/supervisor/conf.d/supervisord.conf"))
    parser.add_argument("--daemon-id")
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()
    if args.image.startswith("-") or any(c.isspace() for c in args.image):
        parser.error("--image must be an image reference")
    source = Path(__file__).resolve().parent
    fixtures = [source / "snapshot_fixture.py", source / "supervisor_fixture.py"]
    run_id = uuid.uuid4().hex
    name = "1panel-supervisor-smoke-" + run_id[:12]
    container = None
    volumes = []
    report = {"image": args.image, "run_id": run_id, "panel_config": args.panel_config,
              "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in fixtures + [Path(__file__).resolve()]},
              "isolation": {"host_binds": [], "privileged": False, "network": "none",
                            "docker_api": "container-only fake socket", "storage": "new anonymous volumes"}}
    with tempfile.TemporaryDirectory(prefix="supervisor-smoke-docker-") as config:
        env = os.environ.copy()
        env["DOCKER_CONFIG"] = config
        env["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            env.pop(key, None)

        def docker(*arguments, timeout=30, check=True):
            result = subprocess.run(["docker", *arguments], env=env, text=True, capture_output=True, timeout=timeout)
            if check and result.returncode:
                raise RuntimeError("docker " + arguments[0] + " failed")
            return result

        def fixture(command, timeout=80):
            result = docker("exec", container, "/usr/bin/python3", "/tmp/supervisor_fixture.py", command,
                            "--panel-config", args.panel_config, timeout=timeout, check=False)
            data = None
            try:
                data = json.loads(result.stdout)
            except ValueError:
                pass
            return {"exit_code": result.returncode, "data": data}

        def state(timeout=30):
            result = docker("inspect", "--format", "{{json .State}}", container, timeout=timeout)
            data = json.loads(result.stdout)
            return {k: data[k] for k in ("Running", "Status", "ExitCode", "Pid", "OOMKilled")}

        def wait_boot():
            deadline = time.monotonic() + 60
            markers = {"supervisor_start_requested": False,
                       "core_spawn_seen": False, "agent_spawn_seen": False}

            def remaining_timeout():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("panel initialization did not reach Supervisor readiness")
                return min(5, remaining)

            while time.monotonic() < deadline:
                current = state(timeout=remaining_timeout())
                if not current["Running"]:
                    raise RuntimeError("container exited before panel initialization completed")
                # Raw logs may contain secrets; retain only fixed startup flags.
                result = docker("logs", "--tail", "200", container, timeout=remaining_timeout())
                raw = result.stdout + result.stderr
                markers["supervisor_start_requested"] |= "启动 Supervisor..." in raw
                markers["core_spawn_seen"] |= "spawned: '1panel-core'" in raw
                markers["agent_spawn_seen"] |= "spawned: '1panel-agent'" in raw
                # Startup lines can leave the log tail before the next poll.
                # The explicit panel socket and live program PIDs prove readiness.
                result = docker("exec", container, "/usr/bin/supervisorctl", "-c", args.panel_config,
                                "status", timeout=remaining_timeout(), check=False)
                running = dict(re.findall(r"^(1panel-(?:core|agent))\s+RUNNING\s+pid (\d+)", result.stdout, re.M))
                if result.returncode == 0 and set(running) == {"1panel-core", "1panel-agent"}:
                    return {"startup_markers": markers, "state": state(timeout=remaining_timeout()),
                            "supervisorctl_config": args.panel_config, "supervisorctl_status_exit": 0,
                            "panel_pids": running}
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
            raise RuntimeError("panel initialization did not reach Supervisor readiness")

        try:
            daemon_id = docker("info", "--format", "{{.ID}}").stdout.strip()
            if args.daemon_id and daemon_id != args.daemon_id:
                raise RuntimeError("Docker daemon differs from the named test target")
            report["daemon_id"] = daemon_id
            report["image_id"] = docker("image", "inspect", "--format", "{{.Id}}", args.image).stdout.strip()
            print(json.dumps({"container": name, "image": args.image, "isolation": report["isolation"]}), flush=True)
            container = docker("create", "--name", name, "--label", LABEL + "=" + run_id,
                               "--network", "none", "--cpus", "1", "--memory", "2g", "--pids-limit", "256",
                               "--security-opt", "no-new-privileges=true", "--env",
                               "DOCKER_HOST=unix:///tmp/snapshot-smoke-docker.sock", "--entrypoint", "/usr/bin/python3",
                               args.image, "/tmp/snapshot_fixture.py", "init", "--generation", "v2").stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{64}", container):
                raise RuntimeError("Docker did not return an exact container ID")
            mounts = json.loads(docker("inspect", "--format", "{{json .Mounts}}", container).stdout)
            if any(m["Type"] != "volume" or not re.fullmatch(r"[0-9a-f]{64}", m.get("Name", "")) for m in mounts):
                raise RuntimeError("fixture requires only fresh anonymous volumes")
            volumes = [m["Name"] for m in mounts]
            for path in fixtures:
                print(json.dumps({"copy_source": str(path), "copy_target": container + ":/tmp/" + path.name,
                                  "file_count": 1, "bytes": path.stat().st_size}), flush=True)
                docker("cp", str(path), container + ":/tmp/" + path.name)
            docker("start", container)
            report["boot"] = wait_boot()
            before = fixture("status")
            report["before"] = before
            if before["exit_code"] or not before["data"]:
                raise RuntimeError("panel was not healthy before tool initialization")
            initialized = fixture("initialize-tool")
            report["initialize_tool"] = initialized
            time.sleep(1)
            report["state_after_initialize"] = state()
            if initialized["exit_code"] or not report["state_after_initialize"]["Running"]:
                raise RuntimeError("tool initialization interrupted the panel container")
            after = fixture("status")
            report["after"] = after
            if after["exit_code"] or after["data"]["panel_pids"] != before["data"]["panel_pids"] or \
                    after["data"]["panel_supervisor_pid"] != before["data"]["panel_supervisor_pid"]:
                raise RuntimeError("tool initialization replaced panel services")
            exercised = fixture("exercise", timeout=180)
            report["exercise"] = exercised
            if exercised["exit_code"] or not (exercised["data"] or {}).get("passed"):
                raise RuntimeError("business Supervisor lifecycle did not preserve panel health")
            report["passed"] = True
        except Exception as error:
            report["passed"] = False
            report["error"] = str(error) if isinstance(error, RuntimeError) else "runner failed (" + type(error).__name__ + ")"
        finally:
            if container and re.fullmatch(r"[0-9a-f]{64}", container):
                try:
                    labels = json.loads(docker("inspect", "--format", "{{json .Config.Labels}}", container).stdout)
                    if labels.get(LABEL) != run_id:
                        raise RuntimeError("cleanup ownership does not match")
                    report["state_before_cleanup"] = state()
                    if not report.get("passed"):
                        try:
                            if report.get("boot") and report["state_before_cleanup"]["Running"]:
                                report["diagnostics"] = fixture("diagnose")
                        except Exception as error:
                            report["diagnostics"] = {"error": "diagnosis failed (" + type(error).__name__ + ")"}
                    print(json.dumps({"cleanup_container": container, "anonymous_volumes": volumes}), flush=True)
                    docker("rm", "--force", "--volumes", container)
                    if any(docker("volume", "inspect", volume, check=False).returncode == 0 for volume in volumes):
                        raise RuntimeError("test anonymous volume remains")
                    report["cleanup"] = "removed only owned container and anonymous volumes"
                except Exception as error:
                    report["passed"] = False
                    report["cleanup_error"] = "cleanup failed (" + type(error).__name__ + ")"
    if args.artifacts_dir:
        target = args.artifacts_dir.resolve()
        target.mkdir(parents=True, exist_ok=True)
        out = target / ("supervisor-" + run_id + ".json")
        with out.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        report["report_path"] = str(out)
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
