#!/usr/bin/env python3
"""Exercise real panel SSH key generation without exporting test private keys."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

import snapshot_fixture as panel

IMAGE_CHECK = Path("/tmp/ssh-image-check.json")


def clean_image():
    home = Path("/root/.ssh")
    if home.is_symlink() or (home.exists() and (not home.is_dir() or any(home.iterdir()))):
        raise panel.SmokeError("image contains build-generated SSH files")
    template = Path("/usr/share/1panel-default/db/agent.db")
    with sqlite3.connect(template.as_uri() + "?mode=ro", uri=True) as db:
        certs = db.execute("SELECT count(*) FROM root_certs").fetchone()[0]
        applied = db.execute("SELECT count(*) FROM migrations WHERE id=?",
                             ("20250905-init-local-ssh",)).fetchone()[0]
        local_ssh = db.execute("SELECT length(value)>0 FROM settings WHERE key=?",
                              ("LocalSSHConn",)).fetchone()
    if certs or applied or (local_ssh and local_ssh[0]):
        raise panel.SmokeError("image template contains stale SSH credentials or initialization state")
    return {"passed": True, "image_ssh_directory_empty": True, "template_root_certs_count": certs,
            "default_key_migration_pending": not bool(applied), "local_ssh_credential_present": False}


def initialize(check_image):
    home = Path("/root/.ssh")
    if check_image:
        IMAGE_CHECK.write_text(json.dumps(clean_image()), encoding="utf-8")
    if home.is_symlink() or (home.exists() and (not home.is_dir() or any(home.iterdir()))):
        raise panel.SmokeError("SSH fixture requires an empty image SSH directory")
    home.mkdir(mode=0o700, exist_ok=True)
    if not (panel.DATA / ".docker_initialized").is_file():
        panel.initialize("v2")
    for key, value in {"ApiInterfaceStatus": "Enable", "ApiKey": secrets.token_hex(24),
                       "ApiKeyValidityTime": "120", "IpWhiteList": "127.0.0.1",
                       "ServerPort": "10086"}.items():
        panel.setting(panel.database("v2"), key, value)
    panel.setting(panel.database("v2", agent=True), "DockerSockPath", "unix://" + panel.SOCKET)
    subprocess.Popen([sys.executable, "/tmp/snapshot_fixture.py", "server", "--generation", "v2"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    panel.wait_for(lambda: Path(panel.SOCKET).is_socket(), 10, "test Docker socket did not start")
    import os
    os.execv("/usr/local/bin/entrypoint.sh", ["/usr/local/bin/entrypoint.sh"])


def generate(name, encryption_mode):
    key = panel.read_setting(panel.database("v2"), "ApiKey")
    timestamp = str(int(time.time()))
    headers = {"Content-Type": "application/json", "1Panel-Timestamp": timestamp,
               "1Panel-Token": hashlib.md5(("1panel" + key + timestamp).encode()).hexdigest(),
               "1Panel-NodeID": "1"}
    payload = {"name": name, "mode": "generate", "encryptionMode": encryption_mode,
               "passPhrase": "", "description": "task-owned SSH persistence test"}
    request = urllib.request.Request("http://127.0.0.1:10086/api/v2/hosts/ssh/cert",
                                     data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status, body = response.status, response.read(1024 * 1024)
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read(1024 * 1024)
    response = json.loads(body)
    message = str(response.get("message", ""))
    result = {"http_status": status, "api_code": response.get("code"),
              "ssh_keygen_available": shutil.which("ssh-keygen") is not None,
              "missing_keygen_error": "ssh-keygen" in message and
                                      any(word in message.lower() for word in ("not found", "executable", "no such"))}
    if status != 200 or response.get("code") != 200:
        return result
    result["api_success"] = True
    return result


def verify(name, encryption_mode, expected_hash=None):
    with sqlite3.connect(panel.database("v2", agent=True).as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT name,encryption_mode,private_key_path,public_key_path FROM root_certs WHERE name=?",
                         (name,)).fetchone()
    if row is None:
        raise panel.SmokeError("generated SSH key metadata is missing")
    if row["encryption_mode"] != encryption_mode:
        raise panel.SmokeError("generated SSH key metadata has an unexpected algorithm")
    private, public = Path(row["private_key_path"]), Path(row["public_key_path"])
    target = panel.DATA.resolve() / "ssh"
    if not Path("/root/.ssh").is_symlink() or Path("/root/.ssh").resolve() != target or \
            private.resolve().parent != target or public.resolve().parent != target:
        raise panel.SmokeError("generated SSH keys are outside persistent panel storage")
    if stat.S_IMODE(private.stat().st_mode) != 0o600 or stat.S_IMODE(target.stat().st_mode) != 0o700:
        raise panel.SmokeError("persistent SSH keys have unsafe permissions")
    public_bytes = public.read_bytes()
    key_types = {"ed25519": {b"ssh-ed25519"}, "rsa": {b"ssh-rsa"},
                 "ecdsa": {b"ecdsa-sha2-nistp256", b"ecdsa-sha2-nistp384", b"ecdsa-sha2-nistp521"}}
    if not public_bytes.split() or public_bytes.split()[0] not in key_types[encryption_mode]:
        raise panel.SmokeError("the public SSH key algorithm differs from the requested algorithm")
    key_hash = hashlib.sha256(public_bytes).hexdigest()
    if expected_hash is not None and key_hash != expected_hash:
        raise panel.SmokeError("SSH public key changed after container recreation")
    derived = subprocess.run(["ssh-keygen", "-y", "-f", str(private)], capture_output=True, timeout=10)
    if derived.returncode or derived.stdout.split()[:2] != public_bytes.split()[:2]:
        raise panel.SmokeError("generated SSH key pair does not match")
    fingerprint = subprocess.run(["ssh-keygen", "-l", "-E", "sha256", "-f", str(public)],
                                 capture_output=True, text=True, timeout=10)
    if fingerprint.returncode:
        raise panel.SmokeError("SSH public fingerprint could not be validated")
    authorized = Path("/root/.ssh/authorized_keys")
    if authorized.resolve().parent != target or stat.S_IMODE(authorized.stat().st_mode) != 0o600 or \
            sum(line.split()[:2] == public_bytes.split()[:2] for line in authorized.read_bytes().splitlines()) != 1:
        raise panel.SmokeError("the generated public key is not authorized exactly once in persistent storage")
    return {"name": name, "encryption_mode": row["encryption_mode"],
            "public_key_type": public_bytes.split()[0].decode("ascii"),
            "private_key_path": str(private), "public_key_path": str(public),
            "persistent_directory": str(target), "private_mode": "0o600", "directory_mode": "0o700",
            "public_key_sha256": key_hash, "public_fingerprint": fingerprint.stdout.split()[1],
            "key_pair_valid": True, "authorized_public_key_persisted": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("init", "create", "verify"))
    parser.add_argument("--name")
    parser.add_argument("--encryption-mode", default="ed25519", choices=("ed25519", "rsa", "ecdsa"))
    parser.add_argument("--public-key-sha256")
    parser.add_argument("--expect-missing-keygen", action="store_true")
    parser.add_argument("--verify-image-clean", action="store_true")
    parser.add_argument("--panel-config", default="/etc/1panel/supervisord.conf",
                        choices=("/etc/1panel/supervisord.conf", "/etc/supervisor/conf.d/supervisord.conf"))
    args = parser.parse_args()
    if args.phase != "init" and (not args.name or not re.fullmatch(r"[a-zA-Z0-9_-]{1,96}", args.name)):
        parser.error("--name must be a plain test key name")
    if args.expect_missing_keygen and args.phase != "create":
        parser.error("--expect-missing-keygen applies only to create")
    panel.PANEL_CONFIGS["v2"] = args.panel_config
    if args.phase == "init":
        initialize(args.verify_image_clean)
        return 0
    result = {"passed": False, "phase": args.phase, "private_key_exported": False}
    try:
        def ready():
            panel.request("v2", "GET", "/health/check", timeout=2)
            return len(panel.services("v2")) == 2 and Path("/etc/1panel/agent.sock").is_socket()

        panel.wait_for(ready, 120, "panel API and Supervisor services did not become ready")
        if IMAGE_CHECK.exists():
            result["image_cleanliness"] = json.loads(IMAGE_CHECK.read_text())
            if result["image_cleanliness"].get("passed") is not True:
                raise panel.SmokeError("image SSH cleanliness preflight did not pass")
        if args.phase == "create":
            result["generation"] = generate(args.name, args.encryption_mode)
            if args.expect_missing_keygen:
                if result["generation"].get("api_success") or \
                        result["generation"]["ssh_keygen_available"] or \
                        not result["generation"]["missing_keygen_error"]:
                    raise panel.SmokeError("baseline did not reproduce the missing ssh-keygen API failure")
                result["missing_dependency_reproduced"] = True
            elif result["generation"].get("api_success") is not True:
                raise panel.SmokeError("real SSH generation API failed")
        if not args.expect_missing_keygen:
            result["key"] = verify(args.name, args.encryption_mode, args.public_key_sha256)
            helper = Path("/usr/local/bin/setup-ssh.py")
            result["setup_helper_sha256"] = hashlib.sha256(helper.read_bytes()).hexdigest()
        result["health_api_200"] = True
        result["passed"] = True
    except Exception as error:
        result["error"] = str(error) if isinstance(error, panel.SmokeError) else "SSH fixture failed (" + type(error).__name__ + ")"
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
