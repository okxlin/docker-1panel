#!/usr/bin/python3
"""Keep panel-generated SSH keys in its persistent application directory."""

from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile


class SetupError(Exception):
    pass


def mountpoints():
    paths = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        value = line.split(" - ", 1)[0].split()[4]
        paths.append(Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)))
    return paths


def directory(path, private=False):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise SetupError("SSH storage requires a trusted directory owned by the panel user")
    if private:
        path.chmod(0o700)
    return info


def entries(path, migration=True):
    result, total = [], 0
    for entry in path.rglob("*"):
        relative, info = entry.relative_to(path), entry.lstat()
        if migration and (len(result) >= 512 or len(relative.parts) > 8):
            raise SetupError("SSH directory exceeds the migration entry or depth limit")
        if info.st_uid != os.geteuid() or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise SetupError("SSH migration accepts only owned regular files and directories")
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise SetupError("SSH migration refuses hard-linked files")
            total += info.st_size
        elif info.st_mode & 0o022:
            raise SetupError("SSH migration refuses directories writable by other users")
        if migration and total > 64 * 1024 * 1024:
            raise SetupError("SSH directory exceeds the migration byte limit")
        result.append((relative, info))
    return result, total


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_nlink, info.st_size,
            info.st_mode, info.st_mtime_ns, info.st_ctime_ns)


def copy_entries(source, staging, manifest):
    # Open every component without following links; keys never pass through logs.
    for relative, expected in sorted(manifest, key=lambda item: len(item[0].parts)):
        destination = staging / relative
        if stat.S_ISDIR(expected.st_mode):
            destination.mkdir(mode=0o700)
            continue
        with ExitStack() as stack:
            parent = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, parent)
            for part in relative.parts[:-1]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                stack.callback(os.close, parent)
            descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            stream = stack.enter_context(os.fdopen(descriptor, "rb"))
            actual = os.fstat(stream.fileno())
            if file_identity(actual) != file_identity(expected):
                raise SetupError("SSH source changed during migration")
            with destination.open("xb") as output:
                remaining = expected.st_size
                while remaining:
                    chunk = stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise SetupError("SSH source changed during migration")
                    output.write(chunk)
                    remaining -= len(chunk)
                if stream.read(1) or file_identity(os.fstat(stream.fileno())) != file_identity(expected):
                    raise SetupError("SSH source changed during migration")
                output.flush()
                os.fchmod(output.fileno(), stat.S_IMODE(expected.st_mode))
                os.fsync(output.fileno())
            if destination.stat().st_size != expected.st_size:
                raise SetupError("SSH file copy is incomplete")
            os.utime(destination, ns=(expected.st_atime_ns, expected.st_mtime_ns))
    for relative, info in reversed(manifest):
        if stat.S_ISDIR(info.st_mode):
            (staging / relative).chmod(stat.S_IMODE(info.st_mode))


def prepare(data_dir, ssh_dir=Path("/root/.ssh")):
    data = Path(data_dir)
    if not data.is_absolute() or data.name != "1panel" or not data.is_dir():
        raise SetupError("SSH setup requires an existing absolute 1panel data directory")
    data = data.resolve(strict=True)
    ssh_dir = Path(ssh_dir)
    if not ssh_dir.is_absolute() or ssh_dir.parent.resolve() != ssh_dir.parent:
        raise SetupError("SSH home directory must have a canonical parent")
    if data == ssh_dir or data.is_relative_to(ssh_dir) or ssh_dir.is_relative_to(data):
        raise SetupError("SSH home and panel data directory must not overlap")
    directory(ssh_dir.parent)
    target, backup = data / "ssh", ssh_dir.with_name(ssh_dir.name + ".before-1panel")
    linked = ssh_dir.is_symlink()
    if linked and os.readlink(ssh_dir) != str(target):
        raise SetupError("SSH home is linked outside the panel key directory")
    mounted = mountpoints()
    if any(path != ssh_dir and path.is_relative_to(ssh_dir) for path in mounted) or \
            any(path != target and path.is_relative_to(target) for path in mounted):
        raise SetupError("SSH setup refuses nested mounts before reading their contents")
    if not linked and any(path != Path("/") and ssh_dir.is_relative_to(path) for path in mounted):
        if not ssh_dir.exists():
            ssh_dir.mkdir(mode=0o700)
        directory(ssh_dir)
        return {"mode": "existing_mount", "ssh_directory": str(ssh_dir)}
    directory(data)
    with open(data / ".ssh-setup.lock", "a", opener=lambda path, flags:
              os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)) as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or \
                info.st_nlink != 1 or info.st_mode & 0o022:
            raise SetupError("SSH setup requires a trusted regular lock file")
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise SetupError("The persistent SSH directory is not a plain directory")
        target_before = target.lstat() if target.exists() else None
        if target.exists():
            directory(target)
            entries(target, migration=False)  # Check types without limiting an established key library.
        if ssh_dir.is_symlink():
            if not target.exists():
                target.mkdir(mode=0o700)
            directory(target, private=True)
            return {"mode": "persistent", "ssh_directory": str(ssh_dir), "storage": str(target)}
        source = []
        if ssh_dir.exists():
            directory(ssh_dir)
            source, size = entries(ssh_dir)
        else:
            size = 0
        if source and target.exists() and any(target.iterdir()):
            raise SetupError("Both SSH directories contain files; refusing to merge or overwrite")
        if ssh_dir.exists() and (backup.exists() or backup.is_symlink()):
            raise SetupError("Prior SSH backup already exists; refusing to overwrite it")
        if source and target in mounted:
            raise SetupError("Existing SSH files require an explicit migration into a mounted key directory")
        if shutil.disk_usage(data).free < size + 1024 * 1024:
            raise SetupError("Not enough space for preserving SSH files")
        print(json.dumps({"ssh_source": str(ssh_dir), "ssh_target": str(target),
                          "copy_entries": len(source), "copy_bytes": size}), flush=True)
        # The link must be renamed within the home filesystem, not across the data volume.
        with tempfile.TemporaryDirectory(prefix=".1panel-ssh-", dir=ssh_dir.parent) as link_home, \
                tempfile.TemporaryDirectory(prefix=".ssh-setup-", dir=data) as temporary:
            temporary = Path(temporary)
            link = Path(link_home) / "link"
            link.symlink_to(target, target_is_directory=True)
            installed_files = None
            if source:
                staging = temporary / "keys"
                staging.mkdir(mode=0o700)
                copy_entries(ssh_dir, staging, source)
                installed_files = {relative: file_identity(info) for relative, info in entries(staging)[0]}
                staging.replace(target)
            elif not target.exists():
                target.mkdir(mode=0o700)
                installed_files = {}
            directory(target, private=True)
            info = target.lstat()
            installed_root = (info.st_dev, info.st_ino)
            original = ssh_dir.exists()
            moved = False
            try:
                if original:
                    ssh_dir.rename(backup)
                    moved = True
                link.replace(ssh_dir)
            except OSError:
                if moved:
                    backup.rename(ssh_dir)
                if installed_files is not None:
                    info = target.lstat()
                    current = {relative: file_identity(info) for relative, info in entries(target)[0]}
                    if (info.st_dev, info.st_ino) != installed_root or current != installed_files:
                        raise SetupError("SSH installation failed; changed key storage was preserved") from None
                    target.rename(temporary / "keys")
                    if target_before is not None:
                        target.mkdir(mode=stat.S_IMODE(target_before.st_mode))
                elif target_before is not None:
                    target.chmod(stat.S_IMODE(target_before.st_mode))
                raise
        return {"mode": "persistent", "ssh_directory": str(ssh_dir), "storage": str(target),
                "prior_directory_preserved": original}


def main():
    if len(sys.argv) != 2:
        print("Usage: setup-ssh.py /absolute/base/1panel", file=sys.stderr)
        return 2
    try:
        print(json.dumps(prepare(Path(sys.argv[1]))), flush=True)
    except (SetupError, OSError) as error:
        message = str(error) if isinstance(error, SetupError) else "SSH setup failed before the panel could start"
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
