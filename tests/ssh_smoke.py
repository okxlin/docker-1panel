#!/usr/bin/env python3
"""Verify V2 SSH generation and storage across two isolated task containers."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

LABEL = "ssh-smoke.run"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--expect-missing-keygen", action="store_true")
    parser.add_argument("--panel-config", default="/etc/1panel/supervisord.conf",
                        choices=("/etc/1panel/supervisord.conf", "/etc/supervisor/conf.d/supervisord.conf"))
    parser.add_argument("--daemon-id")
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()
    if not args.image or args.image.startswith("-") or any(c.isspace() for c in args.image):
        parser.error("--image must be a Docker image reference")
    source = Path(__file__).resolve().parent
    fixtures = [source / "snapshot_fixture.py", source / "ssh_fixture.py"]
    fixture_bytes = {p.name: p.read_bytes() for p in fixtures}
    run_id = uuid.uuid4().hex
    containers, volumes = [], set()
    report = {"image": args.image, "run_id": run_id, "passed": False,
              "expected_missing_keygen": args.expect_missing_keygen, "panel_config": args.panel_config,
              "source_sha256": {name: hashlib.sha256(payload).hexdigest()
                                for name, payload in fixture_bytes.items()},
              "isolation": {"host_binds": [], "privileged": False, "network": "none",
                            "docker_api": "container-only fake socket",
                            "storage": "new task anonymous volumes; only task /opt reused"},
              "private_keys_exported": False}
    report["source_sha256"][Path(__file__).name] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="ssh-smoke-docker-") as config:
        staged = Path(config) / "fixtures"
        staged.mkdir(mode=0o700)
        print(json.dumps({"fixture_source": str(source), "fixture_snapshot": str(staged.resolve()),
                          "file_count": len(fixture_bytes),
                          "bytes": sum(len(payload) for payload in fixture_bytes.values())}), flush=True)
        for name, payload in fixture_bytes.items():
            (staged / name).write_bytes(payload)
        env = os.environ.copy()
        env["DOCKER_CONFIG"] = config
        env["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            env.pop(key, None)

        def docker(*arguments, timeout=30, check=True):
            result = subprocess.run(["docker", *arguments], env=env, text=True,
                                    capture_output=True, timeout=timeout)
            if check and result.returncode:
                raise RuntimeError("docker " + arguments[0] + " failed")
            return result

        def create(ordinal, data_volume=None):
            name = "1panel-ssh-smoke-" + run_id[:12] + "-" + str(ordinal)
            options = ["--mount", "type=volume,src=" + data_volume + ",dst=/opt"] if data_volume else []
            print(json.dumps({"container": name, "image": args.image, "isolation": report["isolation"],
                              "reused_task_volume": data_volume}), flush=True)
            container = docker("create", "--name", name, "--label", LABEL + "=" + run_id,
                               "--network", "none", "--cpus", "1", "--memory", "2g", "--pids-limit", "256",
                               "--security-opt", "no-new-privileges=true", "--env",
                               "DOCKER_HOST=unix:///tmp/snapshot-smoke-docker.sock", *options,
                               "--entrypoint", "/usr/bin/python3", report["image_id"],
                               "/tmp/ssh_fixture.py", "init", "--panel-config", args.panel_config,
                               *([] if args.expect_missing_keygen else ["--verify-image-clean"])).stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{64}", container):
                raise RuntimeError("Docker did not return an exact container ID")
            containers.append(container)
            mounts = json.loads(docker("inspect", "--format", "{{json .Mounts}}", container).stdout)
            if any(m["Type"] != "volume" or not re.fullmatch(r"[0-9a-f]{64}", m.get("Name", ""))
                   for m in mounts):
                raise RuntimeError("SSH fixture requires only task anonymous volumes")
            opt = next((m["Name"] for m in mounts if m["Destination"] == "/opt"), None)
            if not opt or (data_volume and opt != data_volume):
                raise RuntimeError("the task /opt volume is missing or changed")
            volumes.update(m["Name"] for m in mounts)
            for path in (staged / name for name in fixture_bytes):
                print(json.dumps({"copy_source": str(path), "copy_target": container + ":/tmp/" + path.name,
                                  "file_count": 1, "bytes": path.stat().st_size}), flush=True)
                docker("cp", str(path), container + ":/tmp/" + path.name)
            docker("start", container)
            return container, opt

        def fixture(container, phase, name, mode, *options):
            result = docker("exec", container, "/usr/bin/python3", "/tmp/ssh_fixture.py", phase,
                            "--panel-config", args.panel_config, "--name", name,
                            "--encryption-mode", mode, *options, timeout=180, check=False)
            try:
                data = json.loads(result.stdout)
            except ValueError:
                state = json.loads(docker("inspect", "--format", "{{json .State}}", container).stdout)
                logs = docker("logs", "--tail", "80", container, check=False)
                content = result.stderr + logs.stdout + logs.stderr
                errors = ("SyntaxError", "TypeError", "PermissionError", "ModuleNotFoundError",
                          "fixture needs the image template", "SSH fixture requires an empty",
                          "image contains build-generated SSH files",
                          "image template contains stale SSH credentials or initialization state",
                          "SSH storage requires a trusted directory", "SSH home directory must have a canonical parent",
                          "SSH setup failed before the panel could start", "SSH setup refuses nested mounts")
                report["startup_diagnostics"] = {"state": {key: state[key] for key in
                                                        ("Running", "Status", "ExitCode", "OOMKilled")},
                                                 "exec_exit_code": result.returncode,
                                                 "stdout_bytes": len(result.stdout.encode()),
                                                 "matched_error_flags": [key for key in errors if key in content]}
                raise RuntimeError("SSH fixture did not return a structured result") from None
            if result.returncode or data.get("passed") is not True:
                report.setdefault("failed_probe", data)
                raise RuntimeError("SSH " + phase + " fixture failed")
            if not args.expect_missing_keygen and (data.get("image_cleanliness") or {}).get("passed") is not True:
                report.setdefault("failed_probe", data)
                raise RuntimeError("SSH image cleanliness preflight evidence is missing")
            return data

        try:
            report["daemon_id"] = docker("info", "--format", "{{.ID}}").stdout.strip()
            if args.daemon_id and report["daemon_id"] != args.daemon_id:
                raise RuntimeError("Docker daemon differs from the named test target")
            report["image_id"] = docker("image", "inspect", "--format", "{{.Id}}", args.image).stdout.strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", report["image_id"]):
                raise RuntimeError("Docker did not return an immutable image ID")
            first, data_volume = create(1)
            if not args.expect_missing_keygen:
                report["default_key"] = fixture(first, "verify", "id_ed25519_1panel", "ed25519")
            modes = ("ed25519",) if args.expect_missing_keygen else ("ed25519", "rsa", "ecdsa")
            options = ["--expect-missing-keygen"] if args.expect_missing_keygen else []
            generated = {}
            for mode in modes:
                name = "ssh-smoke-" + run_id[:12] + "-" + mode
                generated[name] = fixture(first, "create", name, mode, *options)
            report["generated"] = generated
            if not args.expect_missing_keygen:
                docker("stop", "--time", "20", first, timeout=30)
                second, reused = create(2, data_volume)
                report["default_key_recreated"] = fixture(second, "verify", "id_ed25519_1panel", "ed25519",
                                                           "--public-key-sha256",
                                                           report["default_key"]["key"]["public_key_sha256"])
                report["recreated"] = {name: fixture(second, "verify", name, item["key"]["encryption_mode"],
                                                        "--public-key-sha256", item["key"]["public_key_sha256"])
                                       for name, item in generated.items()}
                report["task_data_volume_reused"] = reused == data_volume
                independent, _ = create(3)
                report["independent_default_key"] = fixture(independent, "verify", "id_ed25519_1panel", "ed25519")
                if report["independent_default_key"]["key"]["public_fingerprint"] == \
                        report["default_key"]["key"]["public_fingerprint"]:
                    raise RuntimeError("independent task data volumes share the same default SSH key")
                report["independent_default_keys_differ"] = True
            report["passed"] = True
        except Exception as error:
            report["error"] = str(error) if isinstance(error, RuntimeError) else \
                "SSH runner failed (" + type(error).__name__ + ")"
        finally:
            try:
                for container in containers:
                    labels = json.loads(docker("inspect", "--format", "{{json .Config.Labels}}", container).stdout)
                    if labels.get(LABEL) != run_id:
                        raise RuntimeError("cleanup container ownership does not match")
                print(json.dumps({"cleanup_containers": containers, "task_anonymous_volumes": sorted(volumes)}), flush=True)
                for container in reversed(containers):
                    docker("rm", "--force", container)
                for volume in sorted(volumes):
                    docker("volume", "rm", volume)
                    if docker("volume", "inspect", volume, check=False).returncode == 0:
                        raise RuntimeError("task anonymous volume remains")
                report["cleanup"] = "removed only owned containers and anonymous volumes"
            except Exception as error:
                report["passed"] = False
                report["cleanup_error"] = "cleanup failed (" + type(error).__name__ + ")"
    if args.artifacts_dir:
        target = args.artifacts_dir.resolve()
        target.mkdir(parents=True, exist_ok=True)
        path = target / ("ssh-" + run_id + ".json")
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(report, indent=2) + "\n")
        report["report_path"] = str(path)
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
