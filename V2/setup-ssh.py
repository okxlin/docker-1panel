#!/usr/bin/python3
"""Keep panel-generated SSH keys in its persistent application directory."""

from contextlib import ExitStack, contextmanager
import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys


class SetupError(Exception):
    pass


def rename_no_replace(source, target, *, src_dir_fd, dst_dir_fd):
    # Linux no-overwrite contract: https://man7.org/linux/man-pages/man2/rename.2.html
    unsupported = "SSH atomic no-replace publication is not supported by this runtime or filesystem"
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError):
        raise SetupError(unsupported) from None
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    if renameat2(src_dir_fd, os.fsencode(source), dst_dir_fd, os.fsencode(target), 1) != 0:
        failure = ctypes.get_errno()
        if failure == errno.EEXIST:
            raise SetupError("SSH publication destination changed; existing storage was preserved")
        if failure in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise SetupError(unsupported)
        raise OSError(failure, os.strerror(failure))


def mountpoints():
    paths = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        value = line.split(" - ", 1)[0].split()[4]
        paths.append(Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)))
    return paths


def bound_directory(path, descriptor, parent_fd=None):
    try:
        current = os.stat(path, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        raise SetupError("SSH directory changed while preparing storage") from None
    opened = os.fstat(descriptor)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino, current.st_uid) != \
            (opened.st_dev, opened.st_ino, opened.st_uid):
        raise SetupError("SSH directory changed while preparing storage")


def directory(path, private=False, trusted_uids=None, *, descriptor=None, parent_fd=None):
    info = os.stat(path, dir_fd=parent_fd, follow_symlinks=False) if descriptor is None else os.fstat(descriptor)
    expected_uid = os.geteuid()
    owners = {expected_uid} if trusted_uids is None else set(trusted_uids)
    reasons = []
    if not stat.S_ISDIR(info.st_mode):
        reasons.append("not a plain directory")
    if info.st_uid not in owners:
        reasons.append("owner UID differs from panel UID" if owners == {expected_uid}
                       else "owner UID is outside trusted storage owners")
    if info.st_mode & 0o022:
        reasons.append("group or other users can write")
    if reasons:
        display = path if parent_fd is None else Path(os.readlink("/proc/self/fd/" + str(parent_fd))) / path
        details = {"path": str(display), "uid": info.st_uid, "expected_uid": expected_uid,
                   "trusted_uids": sorted(owners), "mode": format(stat.S_IMODE(info.st_mode), "04o"),
                   "reasons": reasons}
        raise SetupError("SSH storage requires a trusted directory owned by the panel user: " +
                         json.dumps(details, ensure_ascii=True))
    with ExitStack() as stack:
        if descriptor is None:
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            stack.callback(os.close, descriptor)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_mode) != \
                (info.st_dev, info.st_ino, info.st_uid, info.st_mode):
            raise SetupError("SSH directory changed before metadata preparation")
        bound_directory(path, descriptor, parent_fd)
        if private:
            os.fchmod(descriptor, 0o700)
            actual = os.fstat(descriptor)
            if stat.S_IMODE(actual.st_mode) != 0o700 or actual.st_uid not in owners:
                raise SetupError("SSH storage could not establish a private directory mode")
        bound_directory(path, descriptor, parent_fd)
    return info


def entries(path, migration=True, trusted_uids=None, *, root_fd=None):
    result, total = [], 0
    owners = {os.geteuid()} if trusted_uids is None else set(trusted_uids)

    def walk(descriptor, prefix):
        nonlocal total
        with os.scandir(descriptor) as children:
            for entry in children:
                relative = prefix / entry.name
                info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
                if migration and (len(result) >= 512 or len(relative.parts) > 8):
                    raise SetupError("SSH directory exceeds the migration entry or depth limit")
                if info.st_uid not in owners or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
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
                if stat.S_ISDIR(info.st_mode):
                    nested = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    try:
                        if file_identity(os.fstat(nested)) != file_identity(info):
                            raise SetupError("SSH directory changed during migration")
                        walk(nested, relative)
                    finally:
                        os.close(nested)

    with ExitStack() as stack:
        if root_fd is None:
            root_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, root_fd)
        bound_directory(path, root_fd)
        directory(path, trusted_uids=owners, descriptor=root_fd)
        walk(root_fd, Path())
        bound_directory(path, root_fd)
    return result, total


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_nlink, info.st_size,
            info.st_mode, info.st_mtime_ns, info.st_ctime_ns)


def copy_entries(source, staging, manifest, *, source_fd=None, destination_fd=None, trusted_uids=None):
    # Open every component without following links; keys never pass through logs.
    owners = {os.geteuid()} if trusted_uids is None else set(trusted_uids)
    created_directories = {}

    def parent(root, parts, stack, allowed):
        for part in parts:
            opened = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            stack.callback(os.close, opened)
            directory(part, trusted_uids=allowed, descriptor=opened, parent_fd=root)
            root = opened
        return root

    with ExitStack() as roots:
        if source_fd is None:
            source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            roots.callback(os.close, source_fd)
        if destination_fd is None:
            destination_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            roots.callback(os.close, destination_fd)
        directory(source, descriptor=source_fd)
        directory(staging, private=True, trusted_uids=owners, descriptor=destination_fd)
        for relative, expected in sorted(manifest, key=lambda item: len(item[0].parts)):
            if relative.is_absolute() or not relative.parts or any(part in (".", "..") for part in relative.parts):
                raise SetupError("SSH copy refuses a path outside its directory")
            with ExitStack() as stack:
                destination_parent = parent(destination_fd, relative.parts[:-1], stack, owners)
                if stat.S_ISDIR(expected.st_mode):
                    os.mkdir(relative.name, mode=0o700, dir_fd=destination_parent)
                    opened = os.open(relative.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=destination_parent)
                    stack.callback(os.close, opened)
                    info = directory(relative.name, private=True, trusted_uids=owners,
                                     descriptor=opened, parent_fd=destination_parent)
                    created_directories[relative] = (info.st_dev, info.st_ino)
                    continue
                source_parent = parent(source_fd, relative.parts[:-1], stack, {os.geteuid()})
                descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=source_parent)
                stream = stack.enter_context(os.fdopen(descriptor, "rb"))
                if file_identity(os.fstat(stream.fileno())) != file_identity(expected):
                    raise SetupError("SSH source changed during migration")
                output_fd = os.open(relative.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                                    os.O_NONBLOCK, 0o600, dir_fd=destination_parent)
                output = stack.enter_context(os.fdopen(output_fd, "wb"))
                info = os.fstat(output.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_nlink != 1 or \
                        stat.S_IMODE(info.st_mode) != 0o600:
                    raise SetupError("SSH copy requires a private owned regular destination")
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
                actual = os.fstat(output.fileno())
                if actual.st_size != expected.st_size or stat.S_IMODE(actual.st_mode) != stat.S_IMODE(expected.st_mode) \
                        or actual.st_uid not in owners or actual.st_nlink != 1:
                    raise SetupError("SSH file copy is incomplete or has unsafe metadata")
                os.utime(output.fileno(), ns=(expected.st_atime_ns, expected.st_mtime_ns))
                os.fsync(output.fileno())
        for relative, expected in reversed(manifest):
            if stat.S_ISDIR(expected.st_mode):
                with ExitStack() as stack:
                    opened = parent(destination_fd, relative.parts, stack, owners)
                    info = os.fstat(opened)
                    if (info.st_dev, info.st_ino) != created_directories[relative]:
                        raise SetupError("SSH copy directory changed before metadata restoration")
                    os.fchmod(opened, stat.S_IMODE(expected.st_mode))
                    actual = os.fstat(opened)
                    if stat.S_IMODE(actual.st_mode) != stat.S_IMODE(expected.st_mode) or actual.st_uid not in owners:
                        raise SetupError("SSH copy could not preserve directory mode")
        bound_directory(source, source_fd)
        bound_directory(staging, destination_fd)


def entry_info(name, parent_fd):
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


@contextmanager
def temporary_directory(parent, parent_fd, prefix, owners):
    for _ in range(16):
        name = prefix + secrets.token_hex(12)
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            break
        except FileExistsError:
            continue
    else:
        raise SetupError("SSH setup could not allocate private staging storage")
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    initialized, failed = False, True
    try:
        directory(name, private=True, trusted_uids=owners, descriptor=descriptor, parent_fd=parent_fd)
        initialized, failed = True, False
        try:
            yield parent / name, descriptor
        except BaseException:
            failed = True
            raise
    finally:
        try:
            if initialized:
                bound_directory(name, descriptor, parent_fd)
                if not os.listdir(descriptor):
                    os.rmdir(name, dir_fd=parent_fd)
                elif not failed:
                    raise SetupError("SSH temporary storage changed; its contents were preserved")
                # Failed partial copies stay private. Never recursively remove an unknown tree.
        finally:
            os.close(descriptor)


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
    target, backup = data / "ssh", ssh_dir.with_name(ssh_dir.name + ".before-1panel")
    linked = ssh_dir.is_symlink()
    if linked and os.readlink(ssh_dir) != str(target):
        raise SetupError("SSH home is linked outside the panel key directory")
    mounted = mountpoints()
    if any(path != ssh_dir and path.is_relative_to(ssh_dir) for path in mounted) or \
            any(path != target and path.is_relative_to(target) for path in mounted):
        raise SetupError("SSH setup refuses nested mounts before reading their contents")
    home_mounts = [path for path in mounted if path != Path("/") and ssh_dir.is_relative_to(path)]
    home_owners = {os.geteuid()}
    if home_mounts:
        boundary = max(home_mounts, key=lambda path: len(path.parts))
        home_owners.add(boundary.lstat().st_uid)
    with ExitStack() as handles:
        home_fd = os.open(ssh_dir.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        handles.callback(os.close, home_fd)
        bound_directory(ssh_dir.parent, home_fd)
        if not home_mounts:
            directory(ssh_dir.parent, descriptor=home_fd)
        if home_mounts and not linked:
            if entry_info(ssh_dir.name, home_fd) is None:
                os.mkdir(ssh_dir.name, mode=0o700, dir_fd=home_fd)
            directory(ssh_dir.name, trusted_uids=home_owners, parent_fd=home_fd)
            return {"mode": "existing_mount", "ssh_directory": str(ssh_dir)}

        # Application database/configuration already trust writers to this selected data root.
        # Bind its identity without changing its host-visible owner or parent permissions.
        data_before = data.lstat()
        data_fd = os.open(data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        handles.callback(os.close, data_fd)
        bound_directory(data, data_fd)
        data_info = os.fstat(data_fd)
        if data_before.st_uid != data_info.st_uid:
            raise SetupError("The selected panel data owner changed before SSH setup")
        owners = {os.geteuid(), data_info.st_uid}
        lock_fd = os.open(".ssh-setup.lock", os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW |
                          os.O_NONBLOCK, 0o600, dir_fd=data_fd)
        handles.callback(os.close, lock_fd)
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_nlink != 1 or info.st_mode & 0o022:
            raise SetupError("SSH setup requires a trusted regular lock file")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        def check_roots():
            bound_directory(data, data_fd)
            bound_directory(ssh_dir.parent, home_fd)
            current = entry_info(".ssh-setup.lock", data_fd)
            if current is None or file_identity(current) != file_identity(os.fstat(lock_fd)):
                raise SetupError("SSH setup lock changed during initialization")

        check_roots()
        target_before = entry_info("ssh", data_fd)
        target_fd = None
        if target_before is not None:
            if not stat.S_ISDIR(target_before.st_mode):
                raise SetupError("The persistent SSH directory is not a plain directory")
            target_fd = os.open("ssh", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data_fd)
            handles.callback(os.close, target_fd)
            if (target_before.st_dev, target_before.st_ino) != \
                    (os.fstat(target_fd).st_dev, os.fstat(target_fd).st_ino):
                raise SetupError("SSH target changed before opening")
            directory("ssh", trusted_uids=owners, descriptor=target_fd, parent_fd=data_fd)
            entries(target, migration=False, trusted_uids=owners, root_fd=target_fd)
        current_home = entry_info(ssh_dir.name, home_fd)
        if current_home is not None and stat.S_ISLNK(current_home.st_mode):
            if os.readlink(ssh_dir.name, dir_fd=home_fd) != str(target):
                raise SetupError("SSH home is linked outside the panel key directory")
            if target_fd is None:
                os.mkdir("ssh", mode=0o700, dir_fd=data_fd)
                target_fd = os.open("ssh", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data_fd)
                handles.callback(os.close, target_fd)
            directory("ssh", private=True, trusted_uids=owners, descriptor=target_fd, parent_fd=data_fd)
            check_roots()
            return {"mode": "persistent", "ssh_directory": str(ssh_dir), "storage": str(target)}
        source, size, source_fd = [], 0, None
        if current_home is not None:
            source_fd = os.open(ssh_dir.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
            handles.callback(os.close, source_fd)
            directory(ssh_dir.name, descriptor=source_fd, parent_fd=home_fd)
            source, size = entries(ssh_dir, root_fd=source_fd)
        if source and target_fd is not None and os.listdir(target_fd):
            raise SetupError("Both SSH directories contain files; refusing to merge or overwrite")
        if source_fd is not None and entry_info(backup.name, home_fd) is not None:
            raise SetupError("Prior SSH backup already exists; refusing to overwrite it")
        if source and target in mounted:
            raise SetupError("Existing SSH files require an explicit migration into a mounted key directory")
        space = os.fstatvfs(data_fd)
        if space.f_bavail * space.f_frsize < size + 1024 * 1024:
            raise SetupError("Not enough space for preserving SSH files")
        print(json.dumps({"ssh_source": str(ssh_dir), "ssh_target": str(target),
                          "copy_entries": len(source), "copy_bytes": size}), flush=True)

        with temporary_directory(ssh_dir.parent, home_fd, ".1panel-ssh-", home_owners) as (link_home, link_fd), \
                temporary_directory(data, data_fd, ".ssh-setup-", owners) as (temporary, temporary_fd):
            os.symlink(str(target), "link", dir_fd=link_fd)
            link_info = entry_info("link", link_fd)
            installed_files, installed_fd, prior_empty = None, target_fd, False
            moved, installed_link, published = False, False, False
            try:
                if source:
                    os.mkdir("keys", mode=0o700, dir_fd=temporary_fd)
                    candidate_fd = os.open("keys", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=temporary_fd)
                    handles.callback(os.close, candidate_fd)
                    staging = temporary / "keys"
                    copy_entries(ssh_dir, staging, source, source_fd=source_fd,
                                 destination_fd=candidate_fd, trusted_uids=owners)
                    candidate_files = {relative: file_identity(info) for relative, info in
                                       entries(staging, trusted_uids=owners, root_fd=candidate_fd)[0]}
                    check_roots()
                    bound_directory(temporary.name, temporary_fd, data_fd)
                    current = entry_info("ssh", data_fd)
                    if target_before is None:
                        if current is not None:
                            raise SetupError("SSH target changed before publication")
                    else:
                        bound_directory("ssh", target_fd, data_fd)
                        if os.listdir(target_fd):
                            raise SetupError("SSH target changed before publication")
                        rename_no_replace("ssh", "prior-empty", src_dir_fd=data_fd, dst_dir_fd=temporary_fd)
                        prior_empty = True
                    if entry_info("ssh", data_fd) is not None:
                        raise SetupError("SSH target changed before publication")
                    bound_directory("keys", candidate_fd, temporary_fd)
                    rename_no_replace("keys", "ssh", src_dir_fd=temporary_fd, dst_dir_fd=data_fd)
                    installed_fd, installed_files, published = candidate_fd, candidate_files, True
                elif installed_fd is None:
                    os.mkdir("ssh", mode=0o700, dir_fd=data_fd)
                    installed_fd = os.open("ssh", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data_fd)
                    handles.callback(os.close, installed_fd)
                    installed_files = {}
                    published = True
                directory("ssh", private=True, trusted_uids=owners, descriptor=installed_fd, parent_fd=data_fd)
                check_roots()
                if source_fd is not None:
                    bound_directory(ssh_dir.name, source_fd, home_fd)
                    current_source = {relative: file_identity(info) for relative, info in
                                      entries(ssh_dir, root_fd=source_fd)[0]}
                    if current_source != {relative: file_identity(info) for relative, info in source}:
                        raise SetupError("SSH source changed before installation")
                    if entry_info(backup.name, home_fd) is not None:
                        raise SetupError("Prior SSH backup already exists; refusing to overwrite it")
                    rename_no_replace(ssh_dir.name, backup.name, src_dir_fd=home_fd, dst_dir_fd=home_fd)
                    moved = True
                if entry_info(ssh_dir.name, home_fd) is not None:
                    raise SetupError("SSH home changed before link installation")
                current_link = entry_info("link", link_fd)
                if current_link is None or file_identity(current_link) != file_identity(link_info):
                    raise SetupError("SSH temporary link changed before installation")
                check_roots()
                bound_directory("ssh", installed_fd, data_fd)
                rename_no_replace("link", ssh_dir.name, src_dir_fd=link_fd, dst_dir_fd=home_fd)
                installed_link = True
                check_roots()
                bound_directory("ssh", installed_fd, data_fd)
            except (OSError, SetupError):
                if installed_link:
                    current = entry_info(ssh_dir.name, home_fd)
                    if current is None or (current.st_dev, current.st_ino) != (link_info.st_dev, link_info.st_ino) or \
                            not stat.S_ISLNK(current.st_mode) or os.readlink(ssh_dir.name, dir_fd=home_fd) != str(target):
                        raise SetupError("SSH installation failed; changed SSH home was preserved") from None
                    os.unlink(ssh_dir.name, dir_fd=home_fd)
                if moved:
                    if entry_info(ssh_dir.name, home_fd) is not None:
                        raise SetupError("SSH installation failed; original SSH backup was preserved") from None
                    bound_directory(backup.name, source_fd, home_fd)
                    rename_no_replace(backup.name, ssh_dir.name, src_dir_fd=home_fd, dst_dir_fd=home_fd)
                if published and entry_info("ssh", data_fd) is not None:
                    bound_directory("ssh", installed_fd, data_fd)
                    current = {relative: file_identity(info) for relative, info in
                               entries(target, trusted_uids=owners, root_fd=installed_fd)[0]}
                    if current != installed_files:
                        raise SetupError("SSH installation failed; changed key storage was preserved") from None
                    if entry_info("keys", temporary_fd) is not None:
                        raise SetupError("SSH installation failed; staging storage was preserved") from None
                    rename_no_replace("ssh", "keys", src_dir_fd=data_fd, dst_dir_fd=temporary_fd)
                elif target_before is not None and installed_fd == target_fd and not prior_empty:
                    bound_directory("ssh", target_fd, data_fd)
                    os.fchmod(target_fd, stat.S_IMODE(target_before.st_mode))
                    if stat.S_IMODE(os.fstat(target_fd).st_mode) != stat.S_IMODE(target_before.st_mode):
                        raise SetupError("SSH rollback could not restore the prior directory mode")
                if prior_empty:
                    if entry_info("ssh", data_fd) is not None:
                        raise SetupError("SSH installation failed; prior key storage was preserved") from None
                    bound_directory("prior-empty", target_fd, temporary_fd)
                    rename_no_replace("prior-empty", "ssh", src_dir_fd=temporary_fd, dst_dir_fd=data_fd)
                raise
            if prior_empty:
                bound_directory("prior-empty", target_fd, temporary_fd)
                if os.listdir(target_fd):
                    raise SetupError("Prior SSH directory changed; its contents were preserved")
                os.rmdir("prior-empty", dir_fd=temporary_fd)
        return {"mode": "persistent", "ssh_directory": str(ssh_dir), "storage": str(target),
                "prior_directory_preserved": source_fd is not None}


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
