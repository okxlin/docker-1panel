#!/usr/bin/env python3
"""Container-only fixture for the real 1Panel snapshot APIs; no host Docker access."""

import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socketserver
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import uuid

DATA = Path("/opt/1panel")
SOCKET = "/tmp/snapshot-smoke-docker.sock"
PANEL_CONFIGS = {"v1": "/etc/supervisor/conf.d/supervisord.conf",
                 "v2": "/etc/1panel/supervisord.conf"}


class SmokeError(Exception):
    pass


def database(generation, agent=False):
    name = "1Panel.db" if generation == "v1" else ("agent.db" if agent else "core.db")
    return DATA / "db" / name


def setting(path, key, value):
    with sqlite3.connect(path, timeout=10) as db:
        changed = db.execute("UPDATE settings SET value=? WHERE key=?", (value, key)).rowcount
        if not changed:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, value))


def initialize(generation):
    template = Path("/usr/share/1panel-default")
    if not template.is_dir() or template.is_symlink() or DATA.is_symlink() or \
            (DATA.exists() and any(DATA.iterdir())):
        raise SmokeError("fixture needs the image template and an empty anonymous /opt volume")
    paths = list(template.rglob("*"))
    if any(p.is_symlink() for p in paths):
        raise SmokeError("fixture template must not contain symlinks")
    files = [p for p in paths if p.is_file()]
    print(json.dumps({"copy_source": str(template.resolve()), "copy_target": str(DATA),
                      "file_count": len(files), "bytes": sum(p.stat().st_size for p in files)}), flush=True)
    shutil.copytree(template, DATA, dirs_exist_ok=True)
    for key, value in {"ApiInterfaceStatus": "enable" if generation == "v1" else "Enable",
                       "ApiKey": secrets.token_hex(24), "ApiKeyValidityTime": "120",
                       "IpWhiteList": "127.0.0.1", "ServerPort": "10086"}.items():
        setting(database(generation), key, value)
    if generation == "v2":
        setting(database(generation, agent=True), "DockerSockPath", "unix://" + SOCKET)
    (DATA / ".docker_initialized").touch()  # Skip interactive setup and password output.
    subprocess.Popen([sys.executable, __file__, "server", "--generation", generation],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if Path(SOCKET).is_socket():
            os.execv("/usr/local/bin/entrypoint.sh", ["/usr/local/bin/entrypoint.sh"])
        time.sleep(0.1)
    raise SmokeError("container-only Docker fixture did not start")


class DockerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class DockerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = re.sub(r"^/v[0-9.]+", "", self.path.split("?", 1)[0])
        with Path("/tmp/snapshot-smoke-docker-requests.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps([self.command, path]) + "\n")
        values = {"/_ping": b"OK", "/version": {"Version": "29.5.3", "ApiVersion": "1.53",
                  "MinAPIVersion": "1.40", "Os": "linux", "Arch": "amd64"},
                  "/info": {"ServerVersion": "29.5.3", "Containers": 0, "Images": 0,
                  "DockerRootDir": "/var/lib/docker", "OSType": "linux", "Architecture": "x86_64",
                  "Swarm": {"LocalNodeState": "inactive"}},
                  "/containers/json": [], "/images/json": [], "/networks": [],
                  "/volumes": {"Volumes": [], "Warnings": []}, "/events": b""}
        value = values.get(path)
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(200 if path in values else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("API-Version", "1.53")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_HEAD = do_GET

    def do_POST(self):
        self.send_error(501, "Docker mutations are outside the empty test fixture")

    do_PUT = do_DELETE = do_POST


def read_setting(path, key):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) as db:
        row = db.execute("SELECT value FROM settings WHERE key=? ORDER BY rowid DESC LIMIT 1", (key,)).fetchone()
    return row[0] if row else None


def request(generation, method, path, body=None, timeout=15):
    key = read_setting(database(generation), "ApiKey")
    if not key:
        raise SmokeError("test API key is not configured")
    timestamp = str(int(time.time()))
    headers = {"Content-Type": "application/json", "1Panel-Timestamp": timestamp,
               "1Panel-Token": hashlib.md5(("1panel" + key + timestamp).encode()).hexdigest()}
    if generation == "v2":
        headers["1Panel-NodeID"] = "1"
    url = "http://127.0.0.1:10086/api/" + generation + path
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status, raw = response.status, response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read(4 * 1024 * 1024)
    try:
        response = json.loads(raw)
    except ValueError:
        raise SmokeError(method + " " + path + " returned non-JSON data") from None
    if status != 200 or response.get("code") != 200:
        raise SmokeError(method + " " + path + " did not return HTTP/API 200")
    return response.get("data")


def services(generation):
    names = ["1panel"] if generation == "v1" else ["1panel-core", "1panel-agent"]
    result = subprocess.run(["/usr/bin/supervisorctl", "-c", PANEL_CONFIGS[generation], "status"],
                            text=True, capture_output=True, timeout=5)
    running = dict(re.findall(r"^(1panel(?:-core|-agent)?)\s+RUNNING\s+pid (\d+)", result.stdout, re.M))
    return {name: running[name] for name in names if name in running}


def digest(stream):
    first = stream.read(4)
    value = hashlib.sha256(first)
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    return first == b"\x7fELF", value.hexdigest()


def file_digest(path):
    with path.open("rb") as stream:
        return digest(stream)[1]


def inspect_archive(generation, name):
    if not name or Path(name).name != name or name in (".", ".."):
        raise SmokeError("snapshot name is not a file name")
    path = DATA / "backup/system_snapshot" / (name + ".tar.gz")
    running = services(generation)
    binary_names = ["1panel"] if generation == "v1" else ["1panel-core", "1panel-agent"]
    records = []
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()

        def member(suffix):
            matches = [m for m in members if m.isfile() and
                       tuple(Path(m.name).parts[-len(suffix):]) == suffix]
            if len(matches) != 1:
                raise SmokeError("archive must contain exactly one " + "/".join(suffix))
            return matches[0]

        for binary in binary_names:
            suffix = ("1panel", binary) if generation == "v1" else ("base", binary)
            item = member(suffix)
            with archive.extractfile(item) as stream:
                elf, sha = digest(stream)
            installed = Path("/usr/local/bin") / binary
            if binary not in running or not installed.is_file():
                raise SmokeError("running binary is missing: " + binary)
            if not elf or sha != file_digest(installed) or \
                    sha != file_digest(Path("/proc") / running[binary] / "exe"):
                raise SmokeError("archived " + binary + " must be the running ELF binary")
            records.append({"member": item.name, "is_elf": elf, "sha256": sha})
            service_name = binary if generation == "v1" else binary + ".service"
            suffix = ("1panel", "initscript", service_name) if generation == "v1" else ("base", service_name)
            item = member(suffix)
            installed = Path("/etc/init.d") / service_name
            resource = DATA / "resource/initscript" / service_name
            if not installed.is_file() or not resource.is_file():
                raise SmokeError("snapshot service bridge metadata is missing: " + service_name)
            with archive.extractfile(item) as stream:
                sha = digest(stream)[1]
            mode = stat.S_IMODE(installed.stat().st_mode)
            if sha != file_digest(installed) or sha != file_digest(resource) or \
                    item.mode != mode or stat.S_IMODE(resource.stat().st_mode) != mode or not mode & 0o111:
                raise SmokeError("archived service metadata differs from the installed bridge")
            script = installed.read_bytes()
            bridge = script if generation == "v1" else Path("/usr/local/bin/service").read_bytes()
            if b"supervisorctl" not in bridge or PANEL_CONFIGS[generation].encode() not in bridge or \
                    (generation == "v2" and ("/usr/local/bin/service " + binary).encode() not in script):
                raise SmokeError("snapshot service metadata must use the Supervisor bridge")
            records.append({"member": item.name, "sha256": sha, "mode": oct(mode), "uses_bridge": True})
    return {"archive": path.name, "members": records}


def wait_for(check, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (SmokeError, OSError, sqlite3.Error, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise SmokeError(message)


def snapshot_state(generation, description):
    if generation == "v1":
        data = request(generation, "POST", "/settings/snapshot/search",
                       {"page": 1, "pageSize": 10, "info": "", "orderBy": "created_at", "order": "descending"})
        row = next((item for item in (data or {}).get("items", [])
                    if item.get("description") == description), None)
    else:
        with sqlite3.connect(database(generation, agent=True).as_uri() + "?mode=ro", uri=True, timeout=10) as db:
            db.row_factory = sqlite3.Row
            value = db.execute("SELECT id,name,status FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        row = dict(value) if value else None
    return row if row and row["status"].lower() in ("success", "failed") else None


def probe(generation):
    result = {"generation": generation, "passed": False}
    try:
        expected_services = 1 if generation == "v1" else 2

        def ready():
            request(generation, "GET", "/settings/backup/search" if generation == "v1" else "/health/check", timeout=1)
            return len(services(generation)) == expected_services and \
                (generation == "v1" or Path("/etc/1panel/agent.sock").is_socket())

        wait_for(ready, 120, "panel API and Supervisor services did not become ready")
        description = "snapshot-smoke-" + uuid.uuid4().hex
        if generation == "v1":
            body = {"id": 0, "from": "LOCAL", "defaultDownload": "LOCAL",
                    "description": description, "secret": ""}
        else:
            setting(database(generation, agent=True), "SnapshotSmokeMarker", "before-snapshot")
            body = dict(request(generation, "GET", "/settings/snapshot/load"))
            backups = request(generation, "POST", "/backups/search", {"page": 1, "pageSize": 20, "type": "LOCAL"})
            accounts = (backups or {}).get("items", [])
            if not accounts:
                raise SmokeError("new container has no local backup account")
            account = accounts[0]["id"]
            body.update({"sourceAccountIDs": str(account), "downloadAccountID": account,
                         "description": description, "taskID": str(uuid.uuid4()), "timeout": 15})
            body.update({"with" + kind: True for kind in
                         ("MonitorData", "LoginLog", "OperationLog", "SystemLog", "TaskLog")})
        request(generation, "POST", "/settings/snapshot", body)
        result["snapshot_api_accepted"] = True
        row = wait_for(lambda: snapshot_state(generation, description), 120,
                       "snapshot worker did not reach a terminal state")
        result["terminal_status"] = row["status"]
        if generation == "v1":
            status = request(generation, "POST", "/settings/snapshot/status", {"id": row["id"]})
            result["panel_stage"] = (status or {}).get("panel")
            if result["panel_stage"] != "Done":
                raise SmokeError("V1 snapshot panel stage did not finish with Done")
        if row["status"].lower() != "success":
            raise SmokeError("snapshot did not finish with Success")
        result["archive_inspection"] = inspect_archive(generation, row["name"])
        if generation == "v2":
            before = services(generation)
            if len(before) != 2:
                raise SmokeError("both Supervisor services must be running before recovery")
            setting(database(generation, agent=True), "SnapshotSmokeMarker", "after-snapshot")
            request(generation, "POST", "/settings/snapshot/recover",
                    {"id": row["id"], "isNew": True, "taskID": str(uuid.uuid4()), "secret": ""})
            result["recover_api_accepted"] = True

            def recovered():
                after = services(generation)
                if len(after) != 2 or not all(after.get(name) != pid for name, pid in before.items()):
                    return None
                request(generation, "GET", "/health/check", timeout=1)
                if read_setting(database(generation, agent=True), "SnapshotSmokeMarker") != "before-snapshot":
                    return None
                return after

            # Recovery replaces agent.db, so the snapshot row may disappear by design.
            after = wait_for(recovered, 120, "recovery did not restart both services and restore healthy API/DB state")
            result["recovery"] = {"pids_before": before, "pids_after": after,
                                  "health_api_200": True, "database_marker_restored": True}
        result["passed"] = True
    except SmokeError as error:
        result["error"] = str(error)
    except Exception as error:
        result["error"] = "fixture check failed (" + type(error).__name__ + ")"
    audit = Path("/tmp/snapshot-smoke-docker-requests.jsonl")
    calls = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()] if audit.exists() else []
    result["docker_fixture_requests"] = sorted({tuple(call) for call in calls})
    if result["passed"] and not calls:
        result.update({"passed": False, "error": "empty Docker fixture was not exercised"})
    print(json.dumps(result), flush=True)
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "server", "probe"))
    parser.add_argument("--generation", choices=("v1", "v2"), required=True)
    parser.add_argument("--panel-config", choices=("/etc/supervisor/conf.d/supervisord.conf",
                                                  "/etc/1panel/supervisord.conf"))
    args = parser.parse_args()
    if args.panel_config:
        PANEL_CONFIGS[args.generation] = args.panel_config
    if args.command == "probe":
        return probe(args.generation)
    try:
        if args.command == "init":
            initialize(args.generation)
        else:
            with DockerServer(SOCKET, DockerHandler) as server:
                server.serve_forever()
    except Exception as error:
        # Do not export exception details that might contain authentication data.
        print(json.dumps({"error": "fixture initialization failed (" + type(error).__name__ + ")"}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
