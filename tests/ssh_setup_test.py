#!/usr/bin/env python3
"""Local filesystem contracts for panel SSH storage migration."""

import contextlib
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "V2/setup-ssh.py"
spec = importlib.util.spec_from_file_location("ssh_setup", TOOL)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
read_mountpoints = setup.mountpoints


class StorageContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="1panel-ssh-setup-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.data = self.root / "1panel"
        self.data.mkdir(mode=0o700)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.ssh = self.home / ".ssh"
        self.target = self.data / "ssh"
        self.backup = self.home / ".ssh.before-1panel"
        mounted = patch.object(setup, "mountpoints", return_value=[Path("/")])
        mounted.start()
        self.addCleanup(mounted.stop)
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def seed(self):
        self.ssh.mkdir(mode=0o755)
        key = self.ssh / "id_test"
        key.write_bytes(b"task-owned migration fixture\n")
        key.chmod(0o600)
        return key

    @contextlib.contextmanager
    def metadata_owners(self, owners):
        """Model host-visible UIDs consistently for pathname and inode reads."""
        read_stat, read_lstat, read_fstat = os.stat, os.lstat, os.fstat

        def substitute(info, path):
            owner = owners.get(Path(path))
            if owner is None:
                return info
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            values["st_uid"] = owner
            return SimpleNamespace(**values)

        def substitute_path(info, path, parent=None):
            if isinstance(path, int):
                return substitute(info, os.readlink("/proc/self/fd/" + str(path)))
            full = Path(path) if parent is None else Path(os.readlink("/proc/self/fd/" + str(parent))) / path
            return substitute(info, full)

        def path_stat(path, *args, **kwargs):
            return substitute_path(read_stat(path, *args, **kwargs), path, kwargs.get("dir_fd"))

        def path_lstat(path, *args, **kwargs):
            return substitute_path(read_lstat(path, *args, **kwargs), path, kwargs.get("dir_fd"))

        def inode_stat(descriptor):
            return substitute(read_fstat(descriptor), os.readlink("/proc/self/fd/" + str(descriptor)))

        with patch.object(os, "stat", path_stat), patch.object(os, "lstat", path_lstat), \
                patch.object(os, "fstat", inode_stat):
            yield

    def test_metadata_owners_are_consistent_across_stat_lstat_and_fstat(self):
        before = self.data.stat()
        foreign = before.st_uid + 42
        parent_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, parent_fd)
        data_fd = os.open(self.data, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, data_fd)
        expected = (before.st_dev, before.st_ino, foreign, before.st_gid, before.st_mode)
        with self.metadata_owners({self.data: foreign}):
            for method, info in (
                ("Path.stat", self.data.stat()),
                ("Path.lstat", self.data.lstat()),
                ("os.stat", os.stat(self.data)),
                ("os.stat nofollow", os.stat(self.data, follow_symlinks=False)),
                ("os.lstat", os.lstat(self.data)),
                ("os.stat dir_fd", os.stat(self.data.name, dir_fd=parent_fd)),
                ("os.lstat dir_fd", os.lstat(self.data.name, dir_fd=parent_fd)),
                ("os.stat fd", os.stat(data_fd)),
                ("os.fstat", os.fstat(data_fd)),
            ):
                with self.subTest(method=method):
                    self.assertEqual((info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode),
                                     expected)
        self.assertEqual(self.data.stat(), before)

    def test_fresh_storage_and_empty_home_are_persistent(self):
        for present in (False, True):
            with self.subTest(existing_empty_home=present):
                if present:
                    self.ssh.unlink()
                    self.ssh.mkdir(mode=0o700)
                setup.prepare(self.data, self.ssh)
                self.assertTrue(self.ssh.is_symlink())
                self.assertEqual(self.ssh.resolve(), self.target)
                self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)

    def test_existing_keys_are_preserved_and_setup_is_idempotent(self):
        key = self.seed()
        expected = key.read_bytes()
        setup.prepare(self.data, self.ssh)
        setup.prepare(self.data, self.ssh)
        self.assertEqual(key.read_bytes(), expected)
        self.assertEqual((self.backup / key.name).read_bytes(), expected)
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        self.assertEqual(self.ssh.resolve(), self.target)

    def test_storage_on_another_filesystem(self):
        shared = Path("/dev/shm")
        if not shared.is_dir() or shared.stat().st_dev == self.home.stat().st_dev:
            self.skipTest("a second local filesystem is unavailable")
        temporary = tempfile.TemporaryDirectory(prefix="1panel-ssh-setup-", dir=shared)
        self.addCleanup(temporary.cleanup)
        data = Path(temporary.name) / "1panel"
        data.mkdir(mode=0o700)
        self.seed()
        setup.prepare(data, self.ssh)
        self.assertEqual(self.ssh.resolve(), data / "ssh")
        self.assertTrue((data / "ssh/id_test").is_file())

    def test_two_nonempty_directories_are_not_merged(self):
        key = self.seed()
        self.target.mkdir(mode=0o700)
        existing = self.target / "existing"
        existing.write_bytes(b"existing target fixture\n")
        with self.assertRaisesRegex(setup.SetupError, "Both SSH directories"):
            setup.prepare(self.data, self.ssh)
        self.assertFalse(self.ssh.is_symlink())
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertEqual(existing.read_bytes(), b"existing target fixture\n")

    def test_existing_home_mount_is_not_scanned_or_moved(self):
        self.seed()
        with patch.object(setup, "mountpoints", return_value=[Path("/"), self.home]), \
                patch.object(setup, "entries", side_effect=AssertionError("mount was scanned")):
            result = setup.prepare(self.data, self.ssh)
        self.assertEqual(result["mode"], "existing_mount")
        self.assertFalse(self.ssh.is_symlink())
        self.assertFalse(self.target.exists())

    def test_nested_mount_is_rejected_before_directory_scan(self):
        key = self.seed()
        with patch.object(setup, "mountpoints", return_value=[Path("/"), key]), \
                patch.object(setup, "entries", side_effect=AssertionError("mount was scanned")):
            with self.assertRaisesRegex(setup.SetupError, "nested mounts"):
                setup.prepare(self.data, self.ssh)
        self.assertFalse(self.ssh.is_symlink())

    def test_failed_link_install_restores_original_home(self):
        key = self.seed()
        rename = setup.rename_no_replace

        def fail_link(path, target, **kwargs):
            if path == "link":
                raise PermissionError("injected link installation failure")
            return rename(path, target, **kwargs)

        with patch.object(setup, "rename_no_replace", fail_link):
            with self.assertRaises(PermissionError):
                setup.prepare(self.data, self.ssh)
        self.assertFalse(self.ssh.is_symlink())
        self.assertFalse(self.backup.exists())
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        setup.prepare(self.data, self.ssh)
        self.assertEqual(self.ssh.resolve(), self.target)

    def test_failed_install_restores_existing_empty_target(self):
        self.seed()
        self.target.mkdir(mode=0o750)
        original = self.target.stat()
        rename = setup.rename_no_replace

        def fail_link(path, target, **kwargs):
            if path == "link":
                raise PermissionError("injected link installation failure")
            return rename(path, target, **kwargs)

        with patch.object(setup, "rename_no_replace", fail_link), self.assertRaises(PermissionError):
            setup.prepare(self.data, self.ssh)
        self.assertTrue(self.target.is_dir())
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o750)
        after = self.target.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_uid),
                         (original.st_dev, original.st_ino, original.st_uid))
        setup.prepare(self.data, self.ssh)

    def check_source_change(self, grow):
        key = self.seed()
        manifest, size = setup.entries(self.ssh)
        staging = self.data / "staging"
        staging.mkdir(mode=0o700)
        fdopen = os.fdopen
        mutated = False

        class ChangingReader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def fileno(self):
                return self.stream.fileno()

            def read(self, amount=-1):
                nonlocal mutated
                value = self.stream.read(amount)
                if value and not mutated:
                    mutated = True
                    if grow:
                        with key.open("ab") as output:
                            output.write(b"x" * 1024)
                    else:
                        key.write_bytes(b"x" * size)
                        # Some test filesystems coarsen timestamps; make the change deterministic.
                        info = key.stat()
                        os.utime(key, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
                return value

        def open_stream(descriptor, mode, *args, **kwargs):
            stream = fdopen(descriptor, mode, *args, **kwargs)
            return ChangingReader(stream) if mode == "rb" else stream

        with patch.object(os, "fdopen", side_effect=open_stream):
            with self.assertRaisesRegex(setup.SetupError, "changed|incomplete"):
                setup.copy_entries(self.ssh, staging, manifest)
        self.assertTrue(mutated, "the source mutation must occur during copying")
        self.assertLessEqual((staging / key.name).stat().st_size, size)

    def test_source_growth_cannot_exceed_scanned_copy_budget(self):
        self.check_source_change(grow=True)

    def test_same_size_source_rewrite_is_rejected(self):
        self.check_source_change(grow=False)

    def test_persistent_keys_do_not_inherit_the_migration_entry_limit(self):
        self.target.mkdir(mode=0o700)
        print(json.dumps({"fixture_root": str(self.target.resolve()),
                          "create_empty_files": 513, "file_bytes": 0}), file=sys.stderr)
        for index in range(513):
            (self.target / ("task-key-" + str(index))).touch(mode=0o600)
        self.ssh.symlink_to(self.target, target_is_directory=True)
        setup.prepare(self.data, self.ssh)
        self.assertEqual(self.ssh.resolve(), self.target)
        self.ssh.unlink()
        self.target.rename(self.ssh)
        with self.assertRaisesRegex(setup.SetupError, "entry or depth limit"):
            setup.prepare(self.data, self.ssh)
        self.assertFalse(self.ssh.is_symlink())
        self.assertEqual(len(list(self.ssh.iterdir())), 513)

    def test_existing_backup_is_not_overwritten(self):
        key = self.seed()
        self.backup.mkdir(mode=0o700)
        marker = self.backup / "marker"
        marker.write_bytes(b"prior backup fixture")
        with self.assertRaisesRegex(setup.SetupError, "Prior SSH backup"):
            setup.prepare(self.data, self.ssh)
        self.assertEqual(marker.read_bytes(), b"prior backup fixture")
        self.assertTrue(key.is_file())
        self.assertFalse(self.target.exists())

    def test_unknown_home_link_is_rejected_even_under_a_home_mount(self):
        self.ssh.symlink_to(self.data, target_is_directory=True)
        with patch.object(setup, "mountpoints", return_value=[Path("/"), self.home]):
            with self.assertRaisesRegex(setup.SetupError, "linked outside"):
                setup.prepare(self.data, self.ssh)
        self.assertEqual(os.readlink(self.ssh), str(self.data))

    def test_target_link_is_rejected(self):
        self.seed()
        self.target.symlink_to(self.home, target_is_directory=True)
        with self.assertRaisesRegex(setup.SetupError, "plain directory"):
            setup.prepare(self.data, self.ssh)
        self.assertFalse(self.ssh.is_symlink())
        self.assertEqual(os.readlink(self.target), str(self.home))

    def test_source_links_and_special_files_are_rejected(self):
        key = self.seed()
        link = self.ssh / "link"
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    link.symlink_to(key)
                elif kind == "hardlink":
                    os.link(key, link)
                else:
                    os.mkfifo(link)
                with self.assertRaises(setup.SetupError):
                    setup.prepare(self.data, self.ssh)
                link.unlink()
                self.assertTrue(key.is_file())
                self.assertFalse(self.target.exists())

    def test_lock_links_and_special_files_are_rejected(self):
        key = self.seed()
        lock = self.data / ".ssh-setup.lock"
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    lock.symlink_to(key)
                elif kind == "hardlink":
                    os.link(key, lock)
                else:
                    os.mkfifo(lock)
                with self.assertRaises((setup.SetupError, OSError)):
                    setup.prepare(self.data, self.ssh)
                lock.unlink()
                self.assertFalse(self.ssh.is_symlink())
                self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")

    def test_writable_data_root_keeps_metadata_and_keys_without_an_extra_mount(self):
        key = self.seed()
        for mode in (0o770, 0o777):
            self.data.chmod(mode)
            before = self.data.stat()
            setup.prepare(self.data, self.ssh)
            after = self.data.stat()
            self.assertEqual((after.st_uid, after.st_gid, after.st_mode),
                             (before.st_uid, before.st_gid, before.st_mode))
            self.assertEqual(self.ssh.resolve(), self.target)
            self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)

    def test_foreign_data_root_owner_is_trusted_without_changing_parent_metadata(self):
        foreign = os.geteuid() + 42
        self.data.chmod(0o755)
        with self.metadata_owners({self.data: foreign}):
            setup.prepare(self.data, self.ssh)
            self.assertEqual(self.data.stat().st_uid, foreign)
            self.assertEqual(stat.S_IMODE(self.data.stat().st_mode), 0o755)
        self.assertEqual(self.ssh.resolve(), self.target)

    def test_host_owned_persistent_keys_are_reused_with_the_selected_storage_owner(self):
        foreign = os.geteuid() + 42
        self.target.mkdir(mode=0o700)
        key = self.target / "id_test"
        key.write_bytes(b"persisted host-owner fixture\n")
        key.chmod(0o600)
        self.ssh.symlink_to(self.target, target_is_directory=True)
        with self.metadata_owners({self.data: foreign, self.target: foreign, key: foreign}):
            setup.prepare(self.data, self.ssh)
        self.assertEqual(key.read_bytes(), b"persisted host-owner fixture\n")
        self.assertEqual(self.ssh.resolve(), self.target)

    def test_writable_ssh_leaf_is_still_rejected_under_a_writable_data_root(self):
        self.data.chmod(0o777)
        self.target.mkdir(mode=0o777)
        self.target.chmod(0o777)
        with self.assertRaisesRegex(setup.SetupError, "trusted directory") as error:
            setup.prepare(self.data, self.ssh)
        details = json.loads(str(error.exception).split(": ", 1)[1])
        self.assertEqual(details["path"], str(self.target))
        self.assertEqual(details["mode"], "0777")
        self.assertFalse(self.ssh.is_symlink())

    def test_unrelated_ssh_leaf_owner_is_rejected(self):
        foreign = os.geteuid() + 42
        self.target.mkdir(mode=0o700)
        with self.metadata_owners({self.data: foreign, self.target: foreign + 1}):
            with self.assertRaisesRegex(setup.SetupError, "trusted directory") as error:
                setup.prepare(self.data, self.ssh)
        details = json.loads(str(error.exception).split(": ", 1)[1])
        self.assertEqual(details["path"], str(self.target))
        self.assertFalse(self.ssh.is_symlink())

    def test_writable_lock_is_rejected_without_touching_keys(self):
        key = self.seed()
        self.data.chmod(0o777)
        lock = self.data / ".ssh-setup.lock"
        lock.touch(mode=0o600)
        lock.chmod(0o660)
        with self.assertRaisesRegex(setup.SetupError, "trusted regular lock"):
            setup.prepare(self.data, self.ssh)
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.target.exists())

    def test_existing_foreign_home_mount_uses_its_owner_without_scanning(self):
        key = self.seed()
        foreign = os.geteuid() + 42
        with self.metadata_owners({self.home: foreign, self.ssh: foreign}), \
                patch.object(setup, "mountpoints", return_value=[Path("/"), self.home]), \
                patch.object(setup, "entries", side_effect=AssertionError("mount was scanned")):
            result = setup.prepare(self.data, self.ssh)
        self.assertEqual(result["mode"], "existing_mount")
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.target.exists())

    def test_private_mode_must_be_observable_after_chmod(self):
        self.target.mkdir(mode=0o755)
        with patch.object(os, "chmod", return_value=None), patch.object(os, "fchmod", return_value=None):
            with self.assertRaisesRegex(setup.SetupError, "private|mode"):
                setup.directory(self.target, private=True)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o755)

    def test_private_chmod_cannot_follow_a_replaced_target_link(self):
        self.target.mkdir(mode=0o755)
        outside = self.root / "outside"
        outside.mkdir(mode=0o755)
        moved = self.data / "moved-ssh"
        chmod, fchmod = os.chmod, os.fchmod
        injected = False

        def substitute():
            nonlocal injected
            if not injected:
                injected = True
                self.target.rename(moved)
                self.target.symlink_to(outside, target_is_directory=True)

        def path_chmod(path, mode, *args, **kwargs):
            if Path(path) == self.target:
                substitute()
            return chmod(path, mode, *args, **kwargs)

        def inode_chmod(descriptor, mode):
            if Path(os.readlink("/proc/self/fd/" + str(descriptor))) == self.target:
                substitute()
            return fchmod(descriptor, mode)

        with patch.object(os, "chmod", path_chmod), patch.object(os, "fchmod", inode_chmod):
            with self.assertRaisesRegex(setup.SetupError, "changed"):
                setup.directory(self.target, private=True)
        self.assertTrue(injected)
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o755)

    def test_destination_parent_replacement_cannot_copy_into_an_external_tree(self):
        key = self.seed()
        holder = self.data / "staging-holder"
        holder.mkdir(mode=0o700)
        staging = holder / "keys"
        staging.mkdir(mode=0o700)
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (outside / "keys").mkdir(mode=0o700)
        manifest, _ = setup.entries(self.ssh)
        path_open, inode_open = Path.open, os.open
        injected = False

        def substitute():
            nonlocal injected
            if not injected:
                injected = True
                holder.rename(self.data / "moved-staging")
                holder.symlink_to(outside, target_is_directory=True)

        def file_open(path, *args, **kwargs):
            if path == staging / key.name:
                substitute()
            return path_open(path, *args, **kwargs)

        def descriptor_open(path, flags, mode=0o777, *, dir_fd=None):
            if path == key.name and flags & os.O_CREAT:
                substitute()
            return inode_open(path, flags, mode, dir_fd=dir_fd)

        with patch.object(Path, "open", file_open), patch.object(os, "open", descriptor_open):
            with self.assertRaisesRegex(setup.SetupError, "changed"):
                setup.copy_entries(self.ssh, staging, manifest)
        self.assertTrue(injected)
        self.assertFalse((outside / "keys" / key.name).exists())
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")

    def test_entry_scan_is_bound_to_the_opened_root(self):
        self.target.mkdir(mode=0o700)
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (outside / "foreign-file").write_bytes(b"outside marker\n")
        scandir = os.scandir
        injected = False

        def replace_before_scan(path):
            nonlocal injected
            actual = Path(os.readlink("/proc/self/fd/" + str(path))) if isinstance(path, int) else Path(path)
            if actual == self.target and not injected:
                injected = True
                self.target.rename(self.data / "moved-ssh")
                self.target.symlink_to(outside, target_is_directory=True)
            return scandir(path)

        with patch.object(os, "scandir", replace_before_scan):
            with self.assertRaisesRegex(setup.SetupError, "changed"):
                setup.entries(self.target)
        self.assertTrue(injected)
        self.assertEqual((outside / "foreign-file").read_bytes(), b"outside marker\n")

    def test_replaced_temporary_tree_is_preserved_without_recursive_cleanup(self):
        key = self.seed()
        copy = setup.copy_entries
        marker = None

        def replace_temporary(source, staging, manifest, **kwargs):
            nonlocal marker
            holder = staging.parent
            holder.rename(self.data / "moved-temporary")
            holder.mkdir(mode=0o700)
            (holder / "keys").mkdir(mode=0o700)
            marker = holder / "user-marker"
            marker.write_bytes(b"preserve substituted tree\n")
            return copy(source, staging, manifest, **kwargs)

        with patch.object(setup, "copy_entries", replace_temporary):
            with self.assertRaisesRegex(setup.SetupError, "changed"):
                setup.prepare(self.data, self.ssh)
        self.assertIsNotNone(marker)
        self.assertEqual(marker.read_bytes(), b"preserve substituted tree\n")
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.ssh.is_symlink())

    def test_replaced_empty_target_is_not_overwritten_during_publication(self):
        key = self.seed()
        self.target.mkdir(mode=0o700)
        copy = setup.copy_entries
        replacement_identity = None

        def replace_target(source, staging, manifest, **kwargs):
            nonlocal replacement_identity
            copy(source, staging, manifest, **kwargs)
            self.target.rename(self.data / "old-empty-target")
            self.target.mkdir(mode=0o700)
            info = self.target.stat()
            replacement_identity = (info.st_dev, info.st_ino)

        with patch.object(setup, "copy_entries", replace_target):
            with self.assertRaisesRegex(setup.SetupError, "changed"):
                setup.prepare(self.data, self.ssh)
        info = self.target.stat()
        self.assertEqual((info.st_dev, info.st_ino), replacement_identity)
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.ssh.is_symlink())

    def test_new_empty_target_at_publication_syscall_is_preserved(self):
        key = self.seed()
        rename = setup.rename_no_replace
        replacement_identity = None

        def create_target(path, target, **kwargs):
            nonlocal replacement_identity
            if path == "keys" and target == "ssh":
                self.target.mkdir(mode=0o750)
                info = self.target.stat()
                replacement_identity = (info.st_dev, info.st_ino, info.st_uid, info.st_mode)
            return rename(path, target, **kwargs)

        with patch.object(setup, "rename_no_replace", create_target):
            with self.assertRaisesRegex(setup.SetupError, "destination changed"):
                setup.prepare(self.data, self.ssh)
        info = self.target.stat()
        self.assertIsNotNone(replacement_identity)
        self.assertEqual((info.st_dev, info.st_ino, info.st_uid, info.st_mode), replacement_identity)
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.ssh.is_symlink())
        self.assertFalse(self.backup.exists())

    def test_failure_before_empty_target_move_keeps_original_error_and_inode(self):
        key = self.seed()
        self.target.mkdir(mode=0o750)
        original = self.target.stat()
        rename = setup.rename_no_replace

        def fail_move(path, target, **kwargs):
            if path == "ssh" and target == "prior-empty":
                raise PermissionError("injected prior-empty move failure")
            return rename(path, target, **kwargs)

        with patch.object(setup, "rename_no_replace", fail_move):
            with self.assertRaisesRegex(PermissionError, "prior-empty move failure"):
                setup.prepare(self.data, self.ssh)
        info = self.target.stat()
        self.assertEqual((info.st_dev, info.st_ino, info.st_uid, info.st_mode),
                         (original.st_dev, original.st_ino, original.st_uid, original.st_mode))
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.ssh.is_symlink())
        self.assertFalse(self.backup.exists())

    def test_missing_atomic_rename_symbol_preserves_original_storage(self):
        key = self.seed()
        self.target.mkdir(mode=0o750)
        original = self.target.stat()
        with patch.object(setup.ctypes, "CDLL", return_value=SimpleNamespace()), \
                patch.object(os, "rename", side_effect=AssertionError("plain rename fallback was used")):
            with self.assertRaisesRegex(setup.SetupError, "atomic.*not supported"):
                setup.prepare(self.data, self.ssh)
        info = self.target.stat()
        self.assertEqual((info.st_dev, info.st_ino, info.st_uid, info.st_mode),
                         (original.st_dev, original.st_ino, original.st_uid, original.st_mode))
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.ssh.is_symlink())
        self.assertFalse(self.backup.exists())

    def test_unsupported_atomic_rename_preserves_original_storage(self):
        key = self.seed()
        self.target.mkdir(mode=0o750)
        original = self.target.stat()
        for failure in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            with self.subTest(errno=failure):
                def unsupported(*args):
                    setup.ctypes.set_errno(failure)
                    return -1

                with patch.object(setup.ctypes, "CDLL", return_value=SimpleNamespace(renameat2=unsupported)), \
                        patch.object(os, "rename", side_effect=AssertionError("plain rename fallback was used")):
                    with self.assertRaisesRegex(setup.SetupError, "atomic.*not supported"):
                        setup.prepare(self.data, self.ssh)
                info = self.target.stat()
                self.assertEqual((info.st_dev, info.st_ino, info.st_uid, info.st_mode),
                                 (original.st_dev, original.st_ino, original.st_uid, original.st_mode))
                self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
                self.assertFalse(self.ssh.is_symlink())
                self.assertFalse(self.backup.exists())

    def test_host_owned_existing_lock_is_accepted_with_the_selected_data_owner(self):
        foreign = os.geteuid() + 42
        lock = self.data / ".ssh-setup.lock"
        lock.touch(mode=0o600)
        with self.metadata_owners({self.data: foreign, lock: foreign}):
            setup.prepare(self.data, self.ssh)
            self.assertEqual(lock.stat().st_uid, foreign)
        self.assertEqual(self.ssh.resolve(), self.target)

    def test_unrelated_lock_owner_is_rejected_without_touching_keys(self):
        key = self.seed()
        lock = self.data / ".ssh-setup.lock"
        lock.touch(mode=0o600)
        with self.metadata_owners({lock: os.geteuid() + 42}):
            with self.assertRaisesRegex(setup.SetupError, "trusted regular lock"):
                setup.prepare(self.data, self.ssh)
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.target.exists())

    def test_unrelated_persistent_entry_owner_is_rejected(self):
        foreign = os.geteuid() + 42
        self.target.mkdir(mode=0o700)
        key = self.target / "id_test"
        key.write_bytes(b"existing key fixture\n")
        key.chmod(0o600)
        self.ssh.symlink_to(self.target, target_is_directory=True)
        with self.metadata_owners({self.data: foreign, key: foreign + 1}):
            with self.assertRaisesRegex(setup.SetupError, "owned regular"):
                setup.prepare(self.data, self.ssh)
        self.assertEqual(key.read_bytes(), b"existing key fixture\n")

    def test_ordinary_source_does_not_inherit_foreign_data_owner_trust(self):
        key = self.seed()
        foreign = os.geteuid() + 42
        with self.metadata_owners({self.data: foreign, key: foreign}):
            with self.assertRaisesRegex(setup.SetupError, "owned regular"):
                setup.prepare(self.data, self.ssh)
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.target.exists())

    def test_unrelated_staging_owner_fails_before_copying(self):
        key = self.seed()
        foreign = os.geteuid() + 42
        with self.metadata_owners({self.data: foreign}):
            fstat = os.fstat

            def wrong_owner(descriptor):
                info = fstat(descriptor)
                path = Path(os.readlink("/proc/self/fd/" + str(descriptor)))
                if path.parent == self.data and path.name.startswith(".ssh-setup-"):
                    values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                    values["st_uid"] = foreign + 1
                    return SimpleNamespace(**values)
                return info

            with patch.object(os, "fstat", wrong_owner), \
                    patch.object(setup, "copy_entries", side_effect=AssertionError("copy ran before owner check")):
                with self.assertRaisesRegex(setup.SetupError, "trusted directory"):
                    setup.prepare(self.data, self.ssh)
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.target.exists())

    def test_unrelated_destination_file_owner_cannot_receive_key_bytes(self):
        key = self.seed()
        staging = self.data / "staging"
        staging.mkdir(mode=0o700)
        manifest, _ = setup.entries(self.ssh)
        foreign = os.geteuid() + 42
        fstat = os.fstat

        def wrong_owner(descriptor):
            info = fstat(descriptor)
            if Path(os.readlink("/proc/self/fd/" + str(descriptor))) == staging / key.name:
                values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                values["st_uid"] = foreign + 1
                return SimpleNamespace(**values)
            return info

        with patch.object(os, "fstat", wrong_owner):
            with self.assertRaisesRegex(setup.SetupError, "private owned regular"):
                setup.copy_entries(self.ssh, staging, manifest, trusted_uids={os.geteuid(), foreign})
        self.assertEqual((staging / key.name).stat().st_size, 0)
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")

    def test_existing_foreign_direct_ssh_mount_is_kept_without_migration(self):
        key = self.seed()
        foreign = os.geteuid() + 42
        with self.metadata_owners({self.ssh: foreign}), \
                patch.object(setup, "mountpoints", return_value=[Path("/"), self.ssh]), \
                patch.object(setup, "entries", side_effect=AssertionError("explicit mount was scanned")):
            result = setup.prepare(self.data, self.ssh)
        self.assertEqual(result["mode"], "existing_mount")
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.target.exists())

    def test_directory_replacement_before_open_is_not_chmodded(self):
        self.target.mkdir(mode=0o755)
        open_inode = os.open
        injected = False

        def replace_before_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal injected
            if path == self.target and not injected:
                injected = True
                self.target.rename(self.data / "old-directory")
                self.target.mkdir(mode=0o755)
            return open_inode(path, flags, mode, dir_fd=dir_fd)

        with patch.object(os, "open", replace_before_open):
            with self.assertRaisesRegex(setup.SetupError, "changed"):
                setup.directory(self.target, private=True)
        self.assertTrue(injected)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o755)

    def test_removed_installation_link_restores_original_home_and_keys(self):
        key = self.seed()
        rename = setup.rename_no_replace
        injected = False

        def remove_link(path, target, **kwargs):
            nonlocal injected
            result = rename(path, target, **kwargs)
            if path == self.ssh.name and target == self.backup.name:
                injected = True
                for parent in self.home.glob(".1panel-ssh-*"):
                    (parent / "link").unlink()
            return result

        with patch.object(setup, "rename_no_replace", remove_link):
            with self.assertRaisesRegex(setup.SetupError, "changed"):
                setup.prepare(self.data, self.ssh)
        self.assertTrue(injected)
        self.assertFalse(self.ssh.is_symlink())
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.target.exists())
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")

    def test_source_change_after_copy_preserves_new_source_bytes(self):
        key = self.seed()
        copy = setup.copy_entries

        def rewrite_after_copy(source, staging, manifest, **kwargs):
            copy(source, staging, manifest, **kwargs)
            key.write_bytes(b"new source fixture\n")

        with patch.object(setup, "copy_entries", rewrite_after_copy):
            with self.assertRaisesRegex(setup.SetupError, "source changed"):
                setup.prepare(self.data, self.ssh)
        self.assertFalse(self.ssh.is_symlink())
        self.assertFalse(self.target.exists())
        self.assertEqual(key.read_bytes(), b"new source fixture\n")

    def test_foreign_owner_error_reports_actual_and_expected_uid(self):
        actual_uid = self.data.stat().st_uid
        with patch.object(setup.os, "geteuid", return_value=actual_uid + 1):
            with self.assertRaisesRegex(setup.SetupError, "trusted directory") as error:
                setup.directory(self.data)
        message = str(error.exception)
        self.assertIn(": {", message, "owner rejection must identify both UIDs")
        details = json.loads(message.split(": ", 1)[1])
        self.assertEqual(details["path"], str(self.data))
        self.assertEqual(details["uid"], actual_uid)
        self.assertEqual(details["expected_uid"], actual_uid + 1)
        self.assertEqual(details["reasons"], ["owner UID differs from panel UID"])

    def test_error_paths_are_json_escaped_without_extra_log_lines(self):
        path = self.root / "untrusted\n\u76ee\u5f55"
        path.mkdir(mode=0o700)
        path.chmod(0o777)
        with self.assertRaisesRegex(setup.SetupError, "trusted directory") as error:
            setup.directory(path)
        message = str(error.exception)
        self.assertIn(": {", message, "rejected path must be encoded as JSON")
        self.assertNotIn("\n", message)
        self.assertEqual(json.loads(message.split(": ", 1)[1])["path"], str(path))

    def test_explicit_ssh_mount_accepts_writable_application_data_without_changes(self):
        key = self.seed()
        self.data.chmod(0o777)
        with patch.object(setup, "mountpoints", return_value=[Path("/"), self.ssh]), \
                patch.object(setup, "entries", side_effect=AssertionError("mount was scanned")):
            result = setup.prepare(self.data, self.ssh)
        self.assertEqual(result["mode"], "existing_mount")
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode), 0o777)
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.ssh.is_symlink())
        self.assertFalse(self.target.exists())

    def test_explicit_ssh_mount_still_rejects_writable_ssh_directory(self):
        key = self.seed()
        self.data.chmod(0o777)
        self.ssh.chmod(0o777)
        with patch.object(setup, "mountpoints", return_value=[Path("/"), self.ssh]), \
                patch.object(setup, "entries", side_effect=AssertionError("mount was scanned")):
            with self.assertRaisesRegex(setup.SetupError, "trusted directory"):
                setup.prepare(self.data, self.ssh)
        self.assertEqual(key.read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.ssh.is_symlink())
        self.assertFalse(self.target.exists())

    def test_changed_copy_is_preserved_after_failed_link_install(self):
        key = self.seed()
        rename = setup.rename_no_replace

        def fail_after_change(path, target, **kwargs):
            if path == "link":
                (self.target / "new-file").write_bytes(b"concurrent fixture change")
                raise PermissionError("injected link installation failure")
            return rename(path, target, **kwargs)

        with patch.object(setup, "rename_no_replace", fail_after_change):
            with self.assertRaisesRegex(setup.SetupError, "changed key storage was preserved"):
                setup.prepare(self.data, self.ssh)
        self.assertFalse(self.ssh.is_symlink())
        self.assertTrue(key.is_file())
        self.assertEqual((self.target / "new-file").read_bytes(), b"concurrent fixture change")
        self.assertTrue((self.target / key.name).is_file())

    def test_staging_root_stays_private_and_nested_modes_are_preserved(self):
        self.seed()
        nested = self.ssh / "nested"
        nested.mkdir(mode=0o750)
        (nested / "config").write_bytes(b"nested fixture")
        copy = setup.copy_entries

        def inspect_copy(source, staging, manifest, **kwargs):
            self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.fstat(kwargs["destination_fd"]).st_mode), 0o700)
            copy(source, staging, manifest, **kwargs)
            self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)

        with patch.object(setup, "copy_entries", inspect_copy):
            setup.prepare(self.data, self.ssh)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.target / "nested").stat().st_mode), 0o750)
        self.assertEqual((self.target / "nested/config").read_bytes(), b"nested fixture")

    def test_migration_depth_limit_leaves_original_tree_intact(self):
        key = self.seed()
        nested = self.ssh
        for _ in range(9):
            nested = nested / "nested"
            nested.mkdir(mode=0o700)
        with self.assertRaisesRegex(setup.SetupError, "entry or depth limit"):
            setup.prepare(self.data, self.ssh)
        self.assertTrue(key.is_file())
        self.assertTrue(nested.is_dir())
        self.assertFalse(self.target.exists())

    def test_migration_byte_limit_rejects_a_large_sparse_fixture(self):
        key = self.seed()
        size = 64 * 1024 * 1024 + 1
        print(json.dumps({"sparse_fixture": str(key.resolve()), "logical_bytes": size}), file=sys.stderr)
        with key.open("wb") as output:
            output.truncate(size)
        with self.assertRaisesRegex(setup.SetupError, "migration byte limit"):
            setup.prepare(self.data, self.ssh)
        self.assertEqual(key.stat().st_size, size)
        self.assertFalse(self.target.exists())

    def test_explicit_ssh_mount_is_not_scanned_or_chmodded(self):
        self.seed()
        (self.ssh / "unknown-link").symlink_to(self.data, target_is_directory=True)
        mode = stat.S_IMODE(self.ssh.stat().st_mode)
        with patch.object(setup, "mountpoints", return_value=[Path("/"), self.ssh]), \
                patch.object(setup, "entries", side_effect=AssertionError("mount was scanned")):
            result = setup.prepare(self.data, self.ssh)
        self.assertEqual(result["mode"], "existing_mount")
        self.assertEqual(stat.S_IMODE(self.ssh.stat().st_mode), mode)
        self.assertFalse(self.target.exists())

    def test_mountinfo_escaped_paths_are_decoded(self):
        content = "36 25 0:31 / /tmp/home\\040space rw - tmpfs tmpfs rw\n"
        with patch.object(Path, "read_text", return_value=content):
            self.assertEqual(read_mountpoints(), [Path("/tmp/home space")])

    def assert_overlap_rejected(self, data):
        with patch.object(setup, "mountpoints", side_effect=AssertionError("mounts read before path validation")), \
                patch.object(setup, "entries", side_effect=AssertionError("source scanned before path validation")):
            with self.assertRaisesRegex(setup.SetupError, "must not overlap"):
                setup.prepare(data, self.ssh)
        self.assertFalse(self.ssh.is_symlink())
        self.assertEqual((self.ssh / "id_test").read_bytes(), b"task-owned migration fixture\n")
        self.assertFalse(self.backup.exists())

    def test_panel_data_inside_ssh_home_is_rejected_before_mount_or_scan(self):
        self.seed()
        data = self.ssh / "1panel"
        data.mkdir(mode=0o700)
        self.assert_overlap_rejected(data)

    def test_data_alias_equal_to_ssh_home_is_rejected(self):
        self.seed()
        self.data.rmdir()
        self.data.symlink_to(self.ssh, target_is_directory=True)
        self.assert_overlap_rejected(self.data)

    def test_data_alias_containing_ssh_home_is_rejected(self):
        self.seed()
        self.data.rmdir()
        self.data.symlink_to(self.home, target_is_directory=True)
        self.assert_overlap_rejected(self.data)

    def test_data_alias_nested_in_ssh_home_is_rejected(self):
        self.seed()
        nested = self.ssh / "1panel"
        nested.mkdir(mode=0o700)
        self.data.rmdir()
        self.data.symlink_to(nested, target_is_directory=True)
        self.assert_overlap_rejected(self.data)

    def test_unknown_source_alias_is_refused_before_mount_scan(self):
        self.ssh.symlink_to(self.data, target_is_directory=True)
        with patch.object(setup, "mountpoints", side_effect=AssertionError("mounts read before alias validation")):
            with self.assertRaisesRegex(setup.SetupError, "linked outside"):
                setup.prepare(self.data, self.ssh)
        self.assertEqual(os.readlink(self.ssh), str(self.data))


if __name__ == "__main__":
    unittest.main()
