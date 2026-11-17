#!/usr/bin/env python3
"""Exercise the actual V2 host-tool API without host services or Docker access."""

import argparse
import configparser
import json
from pathlib import Path
import re
import subprocess

import snapshot_fixture as snapshot

BUSINESS_CONFIG = "/etc/supervisor/supervisord.conf"
WORKER = "supervisor-smoke-worker"
PROGRESS = {"events": [], "operation": "setup"}


def diagnostics():
    ini = configparser.ConfigParser(interpolation=None)
    ini.read(BUSINESS_CONFIG)
    process = subprocess.run(["/usr/bin/supervisorctl", "-c", BUSINESS_CONFIG, "status"],
                             text=True, capture_output=True, timeout=5)
    init_script = Path("/etc/init.d/supervisor").read_text()
    safe_fields = {"unix_http_server": ("file", "chmod"),
                   "supervisord": ("logfile", "pidfile", "childlogdir", "nodaemon"),
                   "supervisorctl": ("serverurl",), "include": ("files",)}
    return {"business_config": {section: {key: ini[section][key] for key in keys if key in ini[section]}
                                for section, keys in safe_fields.items() if section in ini},
            "business_pid_file_exists": Path("/var/run/supervisord.pid").exists(),
            "business_ctl_exit": process.returncode,
            "business_ctl_connection_missing": "No such file" in process.stdout + process.stderr,
            "init_command_lines": [line for line in init_script.splitlines() if
                                   any(key in line for key in ("start-stop-daemon", "DAEMON", "PIDFILE"))]}


def panel_state(config):
    snapshot.request("v2", "GET", "/health/check", timeout=2)
    result = subprocess.run(["/usr/bin/supervisorctl", "-c", config, "status"],
                            text=True, capture_output=True, timeout=5)
    running = dict(re.findall(r"^(1panel-(?:core|agent))\s+RUNNING\s+pid (\d+)", result.stdout, re.M))
    if result.returncode or set(running) != {"1panel-core", "1panel-agent"}:
        raise snapshot.SmokeError("both panel services must remain under their Supervisor")
    ini = configparser.ConfigParser(interpolation=None)
    ini.read(config)
    pidfile = Path(ini["supervisord"]["pidfile"])
    return {"panel_supervisor_pid": pidfile.read_text().strip(), "panel_pids": running,
            "panel_config": config, "panel_socket": ini["unix_http_server"]["file"].split(";", 1)[0].strip(),
            "health_api_200": True}


def process_state():
    rows = snapshot.request("v2", "GET", "/hosts/tool/supervisor/process") or []
    row = next((item for item in rows if item.get("name") == WORKER), None)
    states = (row or {}).get("status") or []
    return states[0] if len(states) == 1 else None


def wait_worker(status, operation):
    PROGRESS["operation"] = operation

    def ready():
        state = process_state()
        return state if state and state.get("status") == status else None

    return snapshot.wait_for(ready, 20, "business worker did not reach " + status + " after " + operation)


def wait_business(status):
    def ready():
        info = snapshot.request("v2", "POST", "/hosts/tool/status", {"type": "supervisord"})["config"]
        return {"status": info["status"]} if info.get("status") == status else None

    return snapshot.wait_for(ready, 20, "business Supervisor did not reach " + status)


def exercise(config):
    before = panel_state(config)
    events = PROGRESS["events"]

    def unchanged():
        after = panel_state(config)
        if after != before:
            raise snapshot.SmokeError("business Supervisor operation replaced panel services")
        return after

    def event(operation, state):
        unchanged()
        events.append({"operation": operation, "business_process": state, "panel_unchanged": True})

    event("initialized-business-service", wait_business("running"))
    snapshot.request("v2", "POST", "/hosts/tool/supervisor/process",
                     {"name": WORKER, "operate": "create", "command": "/usr/bin/sleep 600",
                      "user": "root", "dir": "/tmp", "numprocs": "1",
                      "autoRestart": "true", "autoStart": "true", "environment": ""})
    worker = wait_worker("RUNNING", "create")
    event("create", worker)
    for operation, status in [("stop", "STOPPED"), ("start", "RUNNING"), ("restart", "RUNNING")]:
        snapshot.request("v2", "POST", "/hosts/tool/supervisor/process", {"name": WORKER, "operate": operation})
        previous = worker
        worker = wait_worker(status, operation)
        if operation == "restart" and worker.get("PID") == previous.get("PID"):
            raise snapshot.SmokeError("business worker restart did not replace its PID")
        event(operation, worker)
    business_pidfile = Path("/var/run/supervisord.pid")
    business_before = business_pidfile.read_text().strip()
    if business_before == before["panel_supervisor_pid"]:
        raise snapshot.SmokeError("panel and business Supervisor must have different PIDs")
    snapshot.request("v2", "POST", "/hosts/tool/operate", {"type": "supervisord", "operate": "restart"}, timeout=40)
    previous = worker
    worker = wait_worker("RUNNING", "restart-business-service")
    if worker.get("PID") == previous.get("PID"):
        raise snapshot.SmokeError("business service restart did not replace its worker")
    event("restart-business-service", worker)
    business_after = business_pidfile.read_text().strip()
    if business_after == business_before:
        raise snapshot.SmokeError("business daemon did not restart")
    for operation, status in [("stop", "stopped"), ("start", "running")]:
        snapshot.request("v2", "POST", "/hosts/tool/operate", {"type": "supervisord", "operate": operation}, timeout=40)
        event(operation + "-business-service", wait_business(status))
        if operation == "start":
            worker = wait_worker("RUNNING", "start-business-service")
    cli = subprocess.run(["/usr/local/bin/1pctl", "status", "all"], text=True, capture_output=True, timeout=15)
    if cli.returncode or cli.stdout.count("[OK]") != 2 or "[FAIL]" in cli.stdout:
        raise snapshot.SmokeError("1pctl did not report both panel services running")
    for name in ("1panel-core", "1panel-agent"):
        bridge = subprocess.run(["/usr/local/bin/service", name, "status"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if bridge.returncode:
            raise snapshot.SmokeError("service bridge did not reach the panel Supervisor")
    return {"passed": True, "events": events, "panel": unchanged(),
            "business_pids": {"before_restart": business_before, "after_restart": business_after},
            "business_config": BUSINESS_CONFIG, "panel_control_commands_passed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "initialize-tool", "exercise", "diagnose"))
    parser.add_argument("--panel-config", required=True,
                        choices=("/etc/supervisor/conf.d/supervisord.conf", "/etc/1panel/supervisord.conf"))
    args = parser.parse_args()
    try:
        if args.command == "status":
            state = snapshot.wait_for(lambda: panel_state(args.panel_config), 60, "panel did not become healthy")
            state["tool"] = snapshot.request("v2", "POST", "/hosts/tool/status", {"type": "supervisord"})
        elif args.command == "initialize-tool":
            snapshot.request("v2", "POST", "/hosts/tool/init",
                             {"type": "supervisord", "configPath": BUSINESS_CONFIG, "serviceName": "supervisor"}, timeout=40)
            state = {"api_accepted": True}
        elif args.command == "diagnose":
            state = diagnostics()
        else:
            state = exercise(args.panel_config)
        print(json.dumps(state), flush=True)
        return 0
    except Exception as error:
        # SmokeError messages are generated locally; remote response bodies stay private.
        detail = str(error) if isinstance(error, snapshot.SmokeError) else type(error).__name__
        print(json.dumps({"error": "supervisor fixture failed: " + detail, "progress": PROGRESS}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
