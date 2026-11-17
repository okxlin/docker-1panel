#!/usr/bin/env python3
"""Isolated real libvirt/TCG fixture. Never run this against host libvirt."""

import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time

DOMAIN = "libvirt-smoke"
DISK = Path("/var/lib/libvirt/images/libvirt-smoke.qcow2")


def command(*args):
    return subprocess.run(args, text=True, capture_output=True, check=True, timeout=60).stdout


def daemon():
    # Test-only settings: no host mounts, devices, added capabilities or public network.
    # The real host daemon retains its normal QEMU user and security policy.
    Path("/etc/libvirt/qemu.conf").write_text(
        'user = "root"\ngroup = "root"\ndynamic_ownership = 0\nremember_owner = 0\n'
        'security_driver = "none"\nsecurity_default_confined = 0\nsecurity_require_confined = 0\n'
        'namespaces = []\ncgroup_controllers = []\nstdio_handler = "file"\nlock_manager = "nop"\n')
    Path("/etc/libvirt/libvirtd.conf").write_text(
        'auth_unix_rw = "none"\nauth_unix_ro = "none"\nunix_sock_rw_perms = "0700"\n'
        'listen_tls = 0\nlisten_tcp = 0\n')
    DISK.parent.mkdir(parents=True, exist_ok=True)
    Path("/var/lib/libvirt/boot").mkdir(parents=True, exist_ok=True)
    os.environ["LIBVIRT_LOG_OUTPUTS"] = "1:file:/tmp/libvirt-smoke-daemon.log"
    os.execv("/usr/sbin/libvirtd", ["/usr/sbin/libvirtd"])


def seed():
    if DISK.exists() or command("virsh", "-c", "qemu:///system", "list", "--all", "--name").strip():
        raise RuntimeError("seed requires a fresh, empty fixture")
    command("qemu-img", "create", "-f", "qcow2", str(DISK), "1G")
    xml = """<domain type='qemu'>
  <name>libvirt-smoke</name>
  <memory unit='MiB'>128</memory>
  <vcpu>1</vcpu>
  <os><type arch='x86_64' machine='pc'>hvm</type></os>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='/var/lib/libvirt/images/libvirt-smoke.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
    <video><model type='vga'/></video>
    <memballoon model='none'/>
  </devices>
</domain>
"""
    Path("/tmp/libvirt-smoke.xml").write_text(xml)
    command("virsh", "-c", "qemu:///system", "define", "/tmp/libvirt-smoke.xml")
    command("virsh", "-c", "qemu:///system", "start", DOMAIN)
    if command("virsh", "-c", "qemu:///system", "domstate", DOMAIN).strip() != "running":
        raise RuntimeError("TCG guest did not start")
    print(json.dumps({"tcg_guest": DOMAIN, "hardware_acceleration": False, "disk": str(DISK)}))


def probe():
    from snapshot_fixture import request, SmokeError

    for _ in range(90):
        try:
            env = request("v2", "GET", "/vms/environment")
            break
        except (SmokeError, OSError, sqlite3.Error):
            time.sleep(1)
    else:
        raise SmokeError("VM environment API did not become available")
    if not all(env[key] for key in ("helperExist", "storageExist", "libvirtActive")) or \
            env["kvmExist"] or env["ready"]:
        raise SmokeError("real libvirt must connect while missing KVM remains unready")

    request("v2", "POST", "/vms/sync", {})
    info = request("v2", "POST", "/vms/info", {"name": DOMAIN, "checkDisks": True})
    if info["name"] != DOMAIN or info["status"] != "running":
        raise SmokeError("panel did not discover the real TCG guest")
    request("v2", "POST", "/vms/storages", {"name": "smoke-pool", "type": "dir",
            "path": "/var/lib/libvirt/images/smoke-pool", "autoStart": False})
    storages = request("v2", "POST", "/vms/storages/list", {})
    if not any(item["name"] == "smoke-pool" for item in storages):
        raise SmokeError("panel storage creation was not reflected by libvirt")
    # qemu-img runs in the client; the runner checks this file from the daemon.
    command("qemu-img", "create", "-f", "qcow2",
            "/var/lib/libvirt/images/smoke-pool/client.qcow2", "16M")

    states = []
    for operation, expected in (("suspend", "paused"), ("resume", "running"),
                                ("destroy", "shut off"), ("start", "running")):
        request("v2", "POST", "/vms/operate", {"name": DOMAIN, "operate": operation}, timeout=30)
        state = request("v2", "POST", "/vms/info", {"name": DOMAIN, "checkDisks": False})["status"]
        if state != expected:
            raise SmokeError("unexpected VM state after " + operation)
        states.append([operation, state])
    graphics = request("v2", "POST", "/vms/console", {"name": DOMAIN})
    with socket.create_connection((graphics["listen"] or "127.0.0.1", int(graphics["port"])), timeout=5) as conn:
        banner = conn.recv(12).decode("ascii")
    if not banner.startswith("RFB "):
        raise SmokeError("VNC console did not return an RFB banner")
    print(json.dumps({"passed": True, "environment": env, "storage_api_created": True,
                      "lifecycle_states": states, "console_rfb_banner": banner,
                      "hardware_acceleration_verified": False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("daemon", "seed", "probe"))
    args = parser.parse_args()
    try:
        {"daemon": daemon, "seed": seed, "probe": probe}[args.command]()
    except Exception as error:
        # Never export API keys, request headers or container logs.
        print(json.dumps({"passed": False, "error": "fixture failed (" + type(error).__name__ + ")"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
