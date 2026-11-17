#!/usr/bin/env python3
"""Focused contracts for backup source identity and archive coverage."""

import importlib.machinery
import importlib.util
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zlib
from unittest.mock import patch

TOOL = Path(os.environ.get("BACKUP_CHECK_TOOL", Path(__file__).resolve().parents[1] / "V2/backup-check"))
loader = importlib.machinery.SourceFileLoader("backup_check", str(TOOL))
spec = importlib.util.spec_from_loader(loader.name, loader)
check = importlib.util.module_from_spec(spec)
loader.exec_module(check)


class SourceContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="1panel-backup-check-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.data = self.root / "data"
        self.data.mkdir()
        self.panel = [{"Type": "bind", "Source": str(self.data), "Destination": str(self.data)}]

    def mount(self, source=None, **values):
        return {"Type": "bind", "Source": str(source or self.data), "Destination": "/app-data",
                "RW": True, **values}

    def test_readable_same_path_directory(self):
        result = check.check_mount_source("unused", self.mount(), self.panel)
        self.assertEqual(result["status"], "readable-same-path")

    def test_readable_single_file_and_read_only_mount(self):
        source = self.data / "config.ini"
        source.write_text("setting=1\n")
        result = check.check_mount_source("unused", self.mount(source, RW=False), self.panel)
        self.assertTrue(result["read_only"])

    def test_existing_private_directory_is_not_a_positive_control(self):
        with self.assertRaisesRegex(check.CheckError, "same-path"):
            check.check_mount_source("unused", self.mount(), [])

    def test_missing_source_fails(self):
        with self.assertRaisesRegex(check.CheckError, "unavailable"):
            check.check_mount_source("unused", self.mount(self.data / "missing"), self.panel)

    def test_nested_mount_cannot_be_hidden_by_a_valid_parent(self):
        nested = self.data / "nested"
        nested.mkdir()
        different = self.root / "other-host-source"
        different.mkdir()
        panel = self.panel + [{"Type": "bind", "Source": str(different), "Destination": str(nested)}]
        with self.assertRaisesRegex(check.CheckError, "same-path"):
            check.check_mount_source("unused", self.mount(nested), panel)

    def test_symlink_escape_does_not_inherit_parent_coverage(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.data / "link"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(check.CheckError, "same-path"):
            check.check_mount_source("unused", self.mount(link), self.panel)

    def test_file_bind_can_cover_a_resolved_symlink_target(self):
        source = self.root / "source-link"
        target = self.root / "actual-file"
        target.write_text("task-owned file\n")
        source.symlink_to(target)
        panel = [{"Type": "bind", "Source": str(source), "Destination": str(source)}]
        self.assertTrue(check.same_path_mapping(source, panel))

    def test_local_volume_requires_matching_mountpoint(self):
        volume = {"Driver": "local", "Mountpoint": str(self.data)}
        with patch.object(check, "docker_output", return_value=__import__("json").dumps(volume)):
            result = check.check_mount_source("unused", self.mount(Type="volume", Name="task-volume"), self.panel)
        self.assertEqual(result["status"], "readable-same-path")

    def test_remote_volume_driver_is_not_claimed_supported(self):
        volume = {"Driver": "remote-plugin", "Mountpoint": str(self.data)}
        with patch.object(check, "docker_output", return_value=__import__("json").dumps(volume)):
            with self.assertRaisesRegex(check.CheckError, "local-driver"):
                check.check_mount_source("unused", self.mount(Type="volume", Name="task-volume"), self.panel)

    def test_volume_mountpoint_mismatch_fails(self):
        volume = {"Driver": "local", "Mountpoint": str(self.root / "different")}
        with patch.object(check, "docker_output", return_value=__import__("json").dumps(volume)):
            with self.assertRaisesRegex(check.CheckError, "mountpoint"):
                check.check_mount_source("unused", self.mount(Type="volume", Name="task-volume"), self.panel)

    def test_ephemeral_tmpfs_is_explicitly_excluded(self):
        result = check.check_mount_source("unused", self.mount(Type="tmpfs", Source=""), [])
        self.assertEqual(result["status"], "excluded-ephemeral")

    def test_unknown_mount_type_fails(self):
        with self.assertRaisesRegex(check.CheckError, "unsupported"):
            check.check_mount_source("unused", self.mount(Type="unknown"), self.panel)

    def test_arguments_cannot_be_docker_options(self):
        for value in ("--format", "a/b", "a\n", "", None):
            with self.subTest(value=value), self.assertRaises(check.CheckError):
                check.docker_name(value)

    def test_absolute_sources_cannot_contain_parent_traversal(self):
        for value in ("relative", "/data/../other", "\0"):
            with self.subTest(value=value), self.assertRaises(check.CheckError):
                check.absolute_path(value)


class ArchiveContracts(unittest.TestCase):
    ROOT = "backup-target"

    def container_entries(self, kind="bind", payload=True, rw=True):
        inspected = {"Type": kind, "Source": "/task/data", "Destination": "/data", "RW": rw}
        mount = {"type": kind, "source": "/task/data", "destination": "/data", "rw": rw,
                 "status": "backed_up", "backupPath": "mounts/00_data/data"}
        if kind == "volume":
            inspected["Name"] = mount["name"] = "task-volume"
        meta = {"containerID": "a" * 64, "containerName": "target", "mounts": [mount]}
        inspection = {"Id": "a" * 64, "Mounts": [inspected]}
        entries = [(self.ROOT, None), (self.ROOT + "/meta.json", meta),
                   (self.ROOT + "/inspect.json", inspection)]
        if payload:
            entries.append((self.ROOT + "/mounts/00_data/data", None))
        if kind == "volume":
            entries.append((self.ROOT + "/mounts/00_data/volume.json",
                            {"Name": "task-volume", "Mountpoint": "/task/data", "Driver": "local"}))
        return entries

    def archive(self, entries, format=tarfile.USTAR_FORMAT):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=format) as archive:
            for name, value in entries:
                if isinstance(value, tarfile.TarInfo):
                    archive.addfile(value)
                    continue
                info = tarfile.TarInfo(name)
                if value is None:
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                else:
                    raw = json.dumps(value).encode() if isinstance(value, (dict, list)) else value
                    info.size = len(raw)
                    archive.addfile(info, io.BytesIO(raw))
        return gzip.compress(buffer.getvalue(), mtime=0)

    def verify(self, entries, **limits):
        return check.verify_stream(io.BytesIO(self.archive(entries)), check.ArchiveBudget(**limits))

    def test_empty_directory_is_valid_payload(self):
        result = self.verify(self.container_entries())
        self.assertEqual(result["persistent_mounts"], 1)

    def test_single_file_read_only_and_business_metadata_names(self):
        entries = self.container_entries(rw=False)
        entries.extend((self.ROOT + "/mounts/00_data/data/" + name, b"ordinary app bytes")
                       for name in ("config.ini", "meta.json", "inspect.json", "volume.json"))
        self.assertEqual(self.verify(entries)["persistent_mounts"], 1)

    def test_complete_named_volume(self):
        self.assertEqual(self.verify(self.container_entries(kind="volume"))["persistent_mounts"], 1)

    def test_tmpfs_is_explicitly_excluded(self):
        entries = self.container_entries(kind="tmpfs", payload=False)
        entries[1][1]["mounts"][0]["status"] = "skipped"
        result = self.verify(entries)
        self.assertEqual(result["excluded_tmpfs"], ["/data"])
        self.assertEqual(result["persistent_mounts"], 0)

    def test_skipped_persistent_mount_is_not_success(self):
        entries = self.container_entries()
        entries[1][1]["mounts"][0]["status"] = "skipped"
        with self.assertRaisesRegex(check.CheckError, "skipped"):
            self.verify(entries)

    def test_manifest_cannot_omit_an_inspected_mount(self):
        entries = self.container_entries()
        entries[1][1]["mounts"] = []
        with self.assertRaisesRegex(check.CheckError, "omits"):
            self.verify(entries)

    def test_manifest_must_preserve_rw(self):
        entries = self.container_entries()
        entries[1][1]["mounts"][0]["rw"] = False
        with self.assertRaisesRegex(check.CheckError, "differs"):
            self.verify(entries)

    def test_identity_must_be_present(self):
        entries = self.container_entries()
        del entries[1][1]["containerID"]
        del entries[2][1]["Id"]
        with self.assertRaises(check.CheckError):
            self.verify(entries)

    def test_missing_payload_directory(self):
        with self.assertRaisesRegex(check.CheckError, "payload"):
            self.verify(self.container_entries(payload=False))

    def test_named_volume_recovery_metadata_must_match(self):
        for missing in (False, True):
            entries = self.container_entries(kind="volume")
            if missing:
                entries.pop()
            else:
                entries[-1][1]["Mountpoint"] = "/other/data"
            with self.subTest(missing=missing), self.assertRaisesRegex(check.CheckError, "named-volume"):
                self.verify(entries)

    def compose_entries(self):
        names = ["containers/first.tar.gz", "containers/second.tar.gz"]
        return [("project", None), ("project/compose_meta.json",
                {"composeName": "project", "files": [{"backupPath": "compose_files/compose.yml"}],
                 "containers": names}), ("project/compose_files/compose.yml", b"services: {}\n"),
                *[("project/" + name, self.archive(self.container_entries())) for name in names]]

    def test_compose_two_container_archives(self):
        result = self.verify(self.compose_entries())
        self.assertEqual(result["kind"], "compose")
        self.assertEqual(len(result["containers"]), 2)

    def test_compose_declared_file_must_exist(self):
        entries = self.compose_entries()
        entries.pop(2)
        with self.assertRaisesRegex(check.CheckError, "Compose file"):
            self.verify(entries)

    def test_compose_duplicates_cannot_hide_missing_files(self):
        entries = self.compose_entries()
        entries[1][1]["files"] *= 2
        with self.assertRaisesRegex(check.CheckError, "duplicat"):
            self.verify(entries)

    def test_compose_duplicate_or_undeclared_container(self):
        for duplicate in (False, True):
            entries = self.compose_entries()
            if duplicate:
                entries[1][1]["containers"][1] = entries[1][1]["containers"][0]
            else:
                entries[1][1]["containers"].pop()
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(check.CheckError, "manifest"):
                self.verify(entries)

    def test_nested_compose_is_not_supported(self):
        entries = self.compose_entries()
        entries[-1] = (entries[-1][0], self.archive(self.compose_entries()))
        with self.assertRaisesRegex(check.CheckError, "nested"):
            self.verify(entries)

    def test_unsafe_or_duplicate_normalized_path(self):
        for name in ("/absolute", "backup-target/../escape", "backup-target/./meta.json"):
            entries = self.container_entries() + [(name, b"untrusted")]
            with self.subTest(name=name), self.assertRaisesRegex(check.CheckError, "path"):
                self.verify(entries)

    def test_links_and_special_files_fail(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            member = tarfile.TarInfo(self.ROOT + "/unsafe")
            member.type, member.linkname = kind, "meta.json"
            with self.subTest(kind=kind), self.assertRaisesRegex(check.CheckError, "links|special"):
                self.verify(self.container_entries() + [(member.name, member)])

    def test_truncated_and_corrupt_gzip_trailers(self):
        archive = self.archive(self.container_entries())
        for malformed in (archive[:-5], archive[:-8] + bytes([archive[-8] ^ 1]) + archive[-7:]):
            with self.subTest(size=len(malformed)), self.assertRaises((EOFError, OSError, check.CheckError, tarfile.TarError)):
                check.verify_stream(io.BytesIO(malformed), check.ArchiveBudget())

    def test_member_and_byte_budget(self):
        entries = self.container_entries()
        with self.assertRaisesRegex(check.CheckError, "member-header"):
            self.verify(entries, max_members=3)
        with self.assertRaisesRegex(check.CheckError, "decompressed-byte"):
            self.verify(entries, max_bytes=1024)

    def test_extension_metadata_budget_precedes_payload_read(self):
        member = tarfile.TarInfo("PaxHeader")
        member.type, member.size = tarfile.XHDTYPE, check.MAX_METADATA + 1
        archive = gzip.compress(member.tobuf(format=tarfile.USTAR_FORMAT), mtime=0)
        with self.assertRaisesRegex(check.CheckError, "extension metadata"):
            check.verify_stream(io.BytesIO(archive), check.ArchiveBudget())

    def test_standard_pax_and_gnu_long_names_are_supported(self):
        entries = self.container_entries() + [(self.ROOT + "/mounts/00_data/data/" + "a" * 150, b"file bytes")]
        for format in (tarfile.PAX_FORMAT, tarfile.GNU_FORMAT):
            with self.subTest(format=format):
                result = check.verify_stream(io.BytesIO(self.archive(entries, format=format)), check.ArchiveBudget())
                self.assertEqual(result["persistent_mounts"], 1)

    def test_declared_payload_size_is_bounded(self):
        member = tarfile.TarInfo("huge-file")
        member.size = 1024 ** 3
        archive = gzip.compress(member.tobuf(format=tarfile.USTAR_FORMAT), mtime=0)
        with self.assertRaisesRegex(check.CheckError, "decompressed-byte"):
            check.verify_stream(io.BytesIO(archive), check.ArchiveBudget(max_bytes=1024 ** 2))

    def test_tar_terminator_is_required_even_with_valid_gzip(self):
        raw = gzip.decompress(self.archive(self.container_entries()))
        truncated = raw[:5 * 512]
        with self.assertRaises((check.CheckError, tarfile.TarError)):
            check.verify_stream(io.BytesIO(gzip.compress(truncated, mtime=0)), check.ArchiveBudget())

    def test_nested_archives_share_the_byte_budget(self):
        with self.assertRaisesRegex(check.CheckError, "decompressed-byte"):
            self.verify(self.compose_entries(), max_bytes=20000)

    def test_duplicate_json_keys_fail(self):
        entries = self.container_entries()
        entries[1] = (entries[1][0], b'{"containerID":"a","containerID":"b"}')
        with self.assertRaisesRegex(check.CheckError, "duplicate JSON"):
            self.verify(entries)

    def test_bad_deflate_and_deep_json_are_controlled_cli_failures(self):
        entries = self.container_entries()
        entries[1] = (entries[1][0], b'{"nested":' + b'[' * 20000 + b'0' + b']' * 20000 + b'}')
        bad_deflate = b'\x1f\x8b\x08\x00' + bytes(6) + b'\x07' + bytes(8)
        for raw in (bad_deflate, self.archive(entries)):
            with self.subTest(size=len(raw)), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.tar.gz"
                path.write_bytes(raw)
                result = subprocess.run([sys.executable, "-I", "-B", str(TOOL), "verify", str(path)],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout)["status"], "failed")
                self.assertEqual(result.stderr, "")

    def test_late_deflate_corruption_after_valid_tar_is_controlled(self):
        payload = gzip.decompress(self.archive(self.container_entries()))
        payload += bytes(65535 - len(payload))
        header = b'\x1f\x8b\x08\x00' + bytes(6)
        stored = b'\x00\xff\xff\x00\x00' + payload
        trailer = zlib.crc32(payload).to_bytes(4, 'little') + len(payload).to_bytes(4, 'little')
        valid = header + stored + b'\x01\x00\x00\xff\xff' + trailer
        self.assertEqual(check.verify_stream(io.BytesIO(valid), check.ArchiveBudget())["persistent_mounts"], 1)
        malformed = header + stored + b'\x07' + bytes(8)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "late-corruption.tar.gz"
            path.write_bytes(malformed)
            result = subprocess.run([sys.executable, "-I", "-B", str(TOOL), "verify", str(path)],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["status"], "failed")
            self.assertEqual(result.stderr, "")

    def test_malformed_shapes_are_json_failure_without_traceback(self):
        variants = []
        for index, key, value in ((1, "mounts", [None]), (2, "Mounts", [None])):
            entries = self.container_entries()
            entries[index][1][key] = value
            variants.append(entries)
        entries = self.compose_entries()
        entries[1][1]["files"] = [None]
        variants.append(entries)
        for entries in variants:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.tar.gz"
                path.write_bytes(self.archive(entries))
                result = subprocess.run([sys.executable, "-I", "-B", str(TOOL), "verify", str(path)],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout)["status"], "failed")
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
