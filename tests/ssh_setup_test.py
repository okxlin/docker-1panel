#!/usr/bin/env python3
"""Local filesystem contracts for panel SSH storage migration."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
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
        replace = Path.replace

        def fail_link(path, target):
            if path.name == "link":
                raise PermissionError("injected link installation failure")
            return replace(path, target)

        with patch.object(Path, "replace", fail_link):
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
        replace = Path.replace

        def fail_link(path, target):
            if path.name == "link":
                raise PermissionError("injected link installation failure")
            return replace(path, target)

        with patch.object(Path, "replace", fail_link), self.assertRaises(PermissionError):
            setup.prepare(self.data, self.ssh)
        self.assertTrue(self.target.is_dir())
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o750)
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

        with patch.object(os, "fdopen", side_effect=lambda *args, **kwargs:
                          ChangingReader(fdopen(*args, **kwargs))):
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

    def test_writable_data_directory_is_rejected(self):
        self.seed()
        self.data.chmod(0o770)
        with self.assertRaisesRegex(setup.SetupError, "trusted directory"):
            setup.prepare(self.data, self.ssh)
        self.assertFalse(self.ssh.is_symlink())
        self.assertFalse(self.target.exists())

    def test_changed_copy_is_preserved_after_failed_link_install(self):
        key = self.seed()
        replace = Path.replace

        def fail_after_change(path, target):
            if path.name == "link":
                (self.target / "new-file").write_bytes(b"concurrent fixture change")
                raise PermissionError("injected link installation failure")
            return replace(path, target)

        with patch.object(Path, "replace", fail_after_change):
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

        def inspect_copy(source, staging, manifest):
            self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)
            copy(source, staging, manifest)
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
