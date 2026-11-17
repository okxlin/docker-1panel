#!/usr/bin/env python3
"""Real V2 backup APIs with controlled, read-only metadata for task-owned sources."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import sys
import tarfile
import time
import uuid

import snapshot_fixture as snapshot

INFO = Path("/tmp/backup-smoke-info.json")
AUDIT = Path("/tmp/snapshot-smoke-docker-requests.jsonl")
EXPECTED = {"/bind-data": ("sentinel.txt", b"1Panel task-owned bind bytes\n"),
            "/file-data": ("single.txt", b"1Panel task-owned single file bytes\n"),
            "/volume-data": ("sentinel.txt", b"1Panel task-owned volume bytes\n")}


class MetadataHandler(snapshot.DockerHandler):
    info = None

    def do_GET(self):
        path = re.sub(r"^/v[0-9.]+", "", self.path.split("?", 1)[0])
        with AUDIT.open("a", encoding="utf-8") as log:
            log.write(json.dumps([self.command, path]) + "\n")
        app, panel, volume = (self.info[key] for key in ("app", "panel", "volume"))
        network = {"Id": "e" * 64, "Name": "1panel-network", "Driver": "bridge", "Scope": "local"}
        values = {"/_ping": b"OK", "/version": {"Version": "29.5.3", "ApiVersion": "1.53",
                  "MinAPIVersion": "1.40", "Os": "linux", "Arch": "amd64"},
                  "/info": {"ServerVersion": "29.5.3", "Containers": 1, "Images": 1,
                  "DockerRootDir": "/var/lib/docker", "OSType": "linux", "Architecture": "x86_64",
                  "Swarm": {"LocalNodeState": "inactive"}},
                  "/containers/json": [{"Id": app["Id"], "Names": [app["Name"]],
                                        "Labels": app["Config"]["Labels"], "State": "running"}],
                  "/images/json": [], "/networks": [network], "/networks/1panel-network": network,
                  "/volumes": {"Volumes": [volume], "Warnings": []}, "/events": b"",
                  "/volumes/" + volume["Name"]: volume}
        for item in (app, panel):
            for name in (item["Id"], item["Name"].removeprefix("/")):
                values["/containers/" + name + "/json"] = item
        value = values.get(path)
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(200 if path in values else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("API-Version", "1.53")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    do_HEAD = do_GET

    def do_POST(self):
        path = re.sub(r"^/v[0-9.]+", "", self.path.split("?", 1)[0])
        with AUDIT.open("a", encoding="utf-8") as log:
            log.write(json.dumps([self.command, path]) + "\n")
        self.send_error(501, "Docker mutations are outside the read-only backup fixture")

    do_PUT = do_DELETE = do_PATCH = do_POST


def helper_check(helper, *arguments, expected):
    result = subprocess.run([sys.executable, helper, *arguments], capture_output=True, text=True, timeout=30)
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise snapshot.SmokeError("backup helper did not emit structured output") from None
    if value.get("status") != expected or result.returncode != (0 if expected == "ok" else 1) or result.stderr:
        raise snapshot.SmokeError("backup helper control did not have the expected result")
    return value


def task_state(task_id):
    values = {}
    for path, table, key in ((snapshot.database("v2", agent=True), "backup_records", "task_id"),
                             (snapshot.DATA / "db/task.db", "tasks", "id")):
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) as db:
            row = db.execute("SELECT status FROM " + table + " WHERE " + key + "=?", (task_id,)).fetchone()
        values[table] = row[0] if row else None
    if not all(value and value.lower() == "success" for value in values.values()):
        raise snapshot.SmokeError("actual task and backup record did not finish with Success")
    return values


def inspect_container(archive, info, visible):
    members = archive.getmembers()
    if len(members) > 1000 or any(PurePosixPath(item.name).is_absolute() or
            ".." in PurePosixPath(item.name).parts or item.size > 1024 * 1024 for item in members):
        raise snapshot.SmokeError("unexpected fixture archive structure")
    metadata = [item for item in members if item.isfile() and len(PurePosixPath(item.name).parts) == 2
                and PurePosixPath(item.name).name == "meta.json"]
    if len(metadata) != 1 or metadata[0].size > 65536:
        raise snapshot.SmokeError("container archive manifest is missing")
    meta = json.loads(archive.extractfile(metadata[0]).read(65537))
    if meta.get("containerID") != info["app"]["Id"]:
        raise snapshot.SmokeError("archive identity differs from the task-owned target")
    root = PurePosixPath(metadata[0].name).parent
    mounts = meta.get("mounts", [])
    if len(mounts) != len(info["app"]["Mounts"]):
        raise snapshot.SmokeError("archive mount count differs from the target")
    sentinels, statuses = [], []
    for item, expected in zip(mounts, info["app"]["Mounts"]):
        if any(item.get(key, "") != expected.get(source, "") for key, source in
               (("type", "Type"), ("source", "Source"), ("destination", "Destination"), ("rw", "RW"))):
            raise snapshot.SmokeError("archive mount metadata differs from the task source")
        statuses.append({"destination": item["destination"], "status": item["status"]})
        if item["type"] == "tmpfs":
            continue
        status = "backed_up" if visible else "skipped"
        if item.get("status") != status:
            raise snapshot.SmokeError("actual backup did not have the expected source visibility result")
        if not visible:
            continue
        payload = root / item["backupPath"]
        if not any(member.isdir() and PurePosixPath(member.name) == payload for member in members):
            raise snapshot.SmokeError("mount payload directory is absent")
        if item["destination"] in EXPECTED:
            filename, raw = EXPECTED[item["destination"]]
            selected = [member for member in members if member.isfile() and
                        PurePosixPath(member.name) == payload / filename]
            if len(selected) != 1 or archive.extractfile(selected[0]).read(65537) != raw:
                raise snapshot.SmokeError("actual sentinel bytes are missing or differ")
            sentinels.append({"destination": item["destination"], "sha256": hashlib.sha256(raw).hexdigest()})
        else:
            if any(payload in PurePosixPath(member.name).parents for member in members):
                raise snapshot.SmokeError("empty directory unexpectedly contains payload")
        if item["type"] == "volume":
            name = str(payload.parent / "volume.json")
            volume = json.loads(archive.extractfile(archive.getmember(name)).read(65537))
            if volume.get("Name") != info["volume"]["Name"] or volume.get("Mountpoint") != info["volume"]["Mountpoint"]:
                raise snapshot.SmokeError("actual named-volume recovery metadata differs")
    return {"mounts": statuses, "sentinels": sentinels}


def backup(kind, name, info, local_dir, visible, helper):
    task_id = str(uuid.uuid4())
    file_name = "backup-smoke-" + task_id + ".tar.gz"
    start = time.time()
    snapshot.request("v2", "POST", "/backups/backup", {"type": kind, "name": name, "fileName": file_name,
                     "stopBefore": False, "secret": "", "taskID": task_id, "description": "local backup smoke"})

    def finished():
        result = snapshot.request("v2", "POST", "/backups/record/search",
                                  {"page": 1, "pageSize": 20, "type": kind, "name": name, "detailName": ""})
        record = next((row for row in (result or {}).get("items", []) if row.get("taskID") == task_id), None)
        return record if record and record.get("status", "").lower() in ("success", "failed") else None

    record = snapshot.wait_for(finished, 60, "real backup did not finish")
    if record["status"].lower() != "success" or record["fileDir"] != kind + "/" + name or record["fileName"] != file_name:
        raise snapshot.SmokeError("actual backup failed or wrote to an unexpected target")
    path = local_dir / record["fileDir"] / file_name
    if path.is_symlink() or path.resolve() != path or path.stat().st_mtime < start or path.stat().st_size > 2 * 1024 * 1024:
        raise snapshot.SmokeError("backup artifact is stale or outside the task volume")
    result = {"kind": kind, "api_accepted": True, "record_status": record["status"],
              "database": task_state(task_id), "archive_sha256": snapshot.file_digest(path),
              "archive_bytes": path.stat().st_size,
              "verify": helper_check(helper, "verify", str(path), expected="ok" if visible else "failed")}
    with tarfile.open(path, "r:gz") as archive:
        if kind == "container":
            result["payload"] = inspect_container(archive, info, visible)
        else:
            nested = [member for member in archive.getmembers() if member.isfile() and
                      len(PurePosixPath(member.name).parts) == 3 and PurePosixPath(member.name).parts[1] == "containers"]
            if len(nested) != 1:
                raise snapshot.SmokeError("Compose must contain the selected task container once")
            with tarfile.open(fileobj=archive.extractfile(nested[0]), mode="r:gz") as inner:
                result["payload"] = inspect_container(inner, info, True)
            config = [member for member in archive.getmembers() if member.isfile() and
                      PurePosixPath(member.name).name == "00_compose.yml"]
            if len(config) != 1 or archive.extractfile(config[0]).read(65537) != b"services: {}\n":
                raise snapshot.SmokeError("Compose configuration bytes differ")
            result["compose_file_bytes_verified"] = True
    return result


def probe(scenario, helper):
    info = json.loads(INFO.read_text(encoding="utf-8"))
    visible = scenario == "visible"
    result = {"passed": False, "scenario": scenario,
              "evidence_class": "real-panel-runtime-with-controlled-docker-metadata-and-task-owned-host-data",
              "helper_sha256": snapshot.file_digest(Path(helper)), "backups": []}
    try:
        def ready():
            snapshot.request("v2", "GET", "/health/check", timeout=1)
            return len(snapshot.services("v2")) == 2 and Path("/etc/1panel/agent.sock").is_socket()

        snapshot.wait_for(ready, 120, "panel core/agent did not become ready")
        local_dir = Path(snapshot.request("v2", "GET", "/backups/local"))
        if local_dir.resolve() != snapshot.DATA / "backup" or local_dir.is_symlink():
            raise snapshot.SmokeError("backup target must be the task-owned panel volume")
        result["preflight"] = helper_check(helper, "preflight", "--panel-container", info["panel"]["Name"],
                                           "--container", info["app"]["Name"], expected="ok" if visible else "failed")
        if result["helper_sha256"] != info["helper_sha256"]:
            raise snapshot.SmokeError("candidate helper bytes differ from the tested source")
        result["backups"].append(backup("container", info["app"]["Name"].removeprefix("/"),
                                         info, local_dir, visible, helper))
        if visible:
            result["compose_preflight"] = helper_check(helper, "preflight", "--panel-container", info["panel"]["Name"],
                                                        "--compose-project", info["project"], expected="ok")
            result["backups"].append(backup("compose", info["project"], info, local_dir, True, helper))
        else:
            # Existing private files/directories are not host-source coverage; inspect order can vary.
            first = next(mount for mount in info["app"]["Mounts"] if mount["Type"] in ("bind", "volume"))
            source = Path(first["Source"])
            own_volume = source == Path(info["volume"]["Mountpoint"]) and \
                source.name == "_data" and source.parent.name == info["project"] + "-volume"
            own_bind = source.parent == Path(info["source_root"]) and source.name in ("bind", "empty", "single.txt") and \
                source.parent.name == "_data" and source.parent.parent.name == info["project"] + "-sources"
            if not source.is_absolute() or source.resolve() != source or source.exists() or not (own_volume or own_bind):
                raise snapshot.SmokeError("private directory control is outside the task boundary")
            single_file = own_bind and source.name == "single.txt"
            print(json.dumps({"private_source_preview": str(source), "files": int(single_file), "bytes": 0}), flush=True)
            if single_file:
                source.parent.mkdir(parents=True, mode=0o700)
                source.touch(mode=0o600, exist_ok=False)
            else:
                source.mkdir(parents=True, mode=0o700)
            control = helper_check(helper, "preflight", "--panel-container", info["panel"]["Name"],
                                   "--container", info["app"]["Name"], expected="failed")
            if "same-path" not in control.get("error", ""):
                raise snapshot.SmokeError("existing private directory did not exercise the identity check")
            result["private_directory_control"] = control
        result["passed"] = True
    except Exception as error:
        result["error"] = str(error) if isinstance(error, snapshot.SmokeError) else "backup fixture failed (" + type(error).__name__ + ")"
    calls = [json.loads(line) for line in AUDIT.read_text(encoding="utf-8").splitlines()] if AUDIT.is_file() else []
    result["docker_requests"] = sorted({tuple(call) for call in calls})
    if not calls or any(method not in ("GET", "HEAD") for method, path in calls):
        result.update(passed=False, error="unexpected Docker mutation or unexercised metadata fixture")
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "server", "probe"))
    parser.add_argument("--generation", choices=("v2",), default="v2")
    parser.add_argument("--scenario", choices=("missing", "visible"), default="missing")
    parser.add_argument("--helper", default="/usr/local/bin/1panel-backup-check")
    parser.add_argument("--panel-config", choices=("/etc/supervisor/conf.d/supervisord.conf", "/etc/1panel/supervisord.conf"))
    args = parser.parse_args()
    if args.panel_config:
        snapshot.PANEL_CONFIGS["v2"] = args.panel_config
    if args.command == "probe":
        return probe(args.scenario, args.helper)
    if args.command == "init":
        snapshot.__file__ = __file__
        snapshot.initialize("v2")
    else:
        MetadataHandler.info = json.loads(INFO.read_text(encoding="utf-8"))
        with snapshot.DockerServer(snapshot.SOCKET, MetadataHandler) as server:
            server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
