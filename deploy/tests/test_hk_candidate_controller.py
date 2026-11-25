"""Exercise the controller's transaction boundaries in a disposable local directory."""

import errno
import hashlib
import io
import json
import os
import stat
import struct
import sys
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hk-candidate"))
import controller as c


class CandidateControllerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.releases = self.root / "releases"
        self.releases.mkdir()
        self.enterContext(patch.object(c, "DEDICATED_ROOT", self.root))
        self.enterContext(
            patch.object(c, "STAGING_CANDIDATE_ROOT", self.root / "staging" / "hk-rag-candidate")
        )
        self.enterContext(patch.object(c, "ROOT", self.root))
        self.enterContext(patch.object(c, "RELEASES", self.releases))
        self.a = "sn-hkc-" + "1" * 12 + "-" + "a" * 12
        self.b = "sn-hkc-" + "2" * 12 + "-" + "b" * 12
        for project in (self.a, self.b):
            (self.releases / project).mkdir()
        self.stop = self.enterContext(patch.object(c, "stop"))
        self.activate = self.enterContext(patch.object(c, "activate"))
        self.compose = self.enterContext(
            patch.object(c, "compose", return_value=Mock(stdout="1\n2\n3\n4\n"))
        )

    def assert_repeatable_private_init(self):
        root = self.root / "fresh-root"
        with (
            patch.object(c, "ROOT", root),
            patch.object(c, "RELEASES", root / "releases"),
        ):
            c.init()
            credentials = root / "secrets.env"
            first = credentials.read_bytes()
            self.assertTrue(first)
            c.init()
            self.assertEqual(first, credentials.read_bytes())
            self.assertEqual(stat.S_IMODE(credentials.stat().st_mode), 0o600)
            for folder in (root, root / "releases"):
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)

    def test_repeat_init_without_relying_on_umask(self):
        self.addCleanup(os.umask, os.umask(0))
        self.assert_repeatable_private_init()

    def test_repeat_init_with_inherited_default_acl(self):
        # POSIX ACL xattr v2: owner rwx, owning group rwx, others none. Under this
        # directory open(..., 0666) creates a 0660 file even with process umask 077.
        acl_entries = ((0x01, 7), (0x04, 7), (0x20, 0))
        acl = struct.pack("<I", 2) + b"".join(
            struct.pack("<HHI", tag, permissions, 0xFFFFFFFF) for tag, permissions in acl_entries
        )
        try:
            os.setxattr(self.root, "system.posix_acl_default", acl)
        except OSError as exc:
            if exc.errno != errno.EOPNOTSUPP:
                raise
            self.skipTest("This temporary filesystem does not support POSIX default ACLs")
        self.addCleanup(os.umask, os.umask(0o077))
        self.assert_repeatable_private_init()

    def test_fixed_root_follows_existing_history_and_rejects_conflicts(self):
        shared = self.root / "staging" / "hk-rag-candidate"
        legacy = self.root / "dedicated"
        with patch.object(c, "DEDICATED_ROOT", legacy):
            self.assertEqual(c.candidate_root("candidate"), legacy)
            self.assertEqual(c.candidate_root("existing-staging"), shared)
            legacy.mkdir()
            (legacy / "current").write_text(self.a)
            self.assertEqual(c.candidate_root("existing-staging"), legacy)
            shared.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "root conflict"):
                c.candidate_root("existing-staging")
            self.assertEqual((legacy / "current").read_text(), self.a)

    def test_existing_staging_cli_reuses_subdirectory_for_init_and_stop(self):
        shared = c.STAGING_CANDIDATE_ROOT
        shared.parent.mkdir()
        lock = shared.parent / ".deploy-staging.lock"
        lock.touch()
        stable_env = shared.parent / ".env"
        stable_env.write_text("stable configuration")
        with (
            patch.object(c, "DEDICATED_ROOT", self.root / "absent-dedicated"),
            patch.object(c, "SHARED_LOCK", lock),
            patch("sys.argv", ["ctrl", "--ssh-profile", "existing-staging", "init"]) as argv,
        ):
            c.main()
            credentials = (shared / "secrets.env").read_bytes()
            c.main()
            self.assertEqual((shared / "secrets.env").read_bytes(), credentials)
            (shared / "releases" / self.a).mkdir()
            (shared / "current").write_text(self.a)
            argv[-1] = "stop"
            c.main()
            self.stop.assert_called_once_with(self.a)
            self.assertTrue((shared / "releases" / self.a / "stopped").is_file())
            self.assertEqual(stable_env.read_text(), "stable configuration")
            with self.assertRaisesRegex(ValueError, "root conflict"):
                c.candidate_root("candidate")

    def test_first_failure_never_starts_a_previous_project(self):
        with self.assertRaisesRegex(RuntimeError, "migration"):
            c.switch(None, self.a, Mock(side_effect=RuntimeError("migration failed")))
        self.assertIsNone(c.current())
        self.stop.assert_called_once_with(self.a)
        self.activate.assert_not_called()
        self.assertFalse((self.root / "pending.json").exists())

    def test_failed_update_restores_old_project_and_stopped_state(self):
        c.atomic(self.root / "current", self.a)
        with self.assertRaises(RuntimeError):
            c.switch(self.a, self.b, Mock(side_effect=RuntimeError("health")))
        self.assertEqual(c.current(), self.a)
        self.assertEqual([x.args for x in self.stop.call_args_list], [(self.a,), (self.b,)])
        self.activate.assert_called_once_with(self.a)
        self.activate.reset_mock()
        (self.releases / self.a / "stopped").touch()
        with self.assertRaises(RuntimeError):
            c.switch(self.a, self.b, Mock(side_effect=RuntimeError("health")))
        self.activate.assert_not_called()

    def test_interrupted_switch_and_manual_rollback(self):
        c.atomic(self.root / "current", self.a)
        c.atomic(
            self.root / "pending.json",
            json.dumps(dict(before=self.a, after=self.b, was_running=True)),
        )
        c.recover()
        self.activate.assert_called_once_with(self.a)
        c.switch(self.a, self.b, lambda: self.activate(self.b))
        self.assertEqual(c.current(), self.b)
        c.atomic(self.releases / self.b / "manifest.json", json.dumps({"previous": self.a}))
        with (
            patch("sys.argv", ["controller.py", "rollback"]),
            patch.object(c, "init"),
            patch.object(c, "SHARED_LOCK", self.root / "absent" / ".deploy-staging.lock"),
            patch.object(c, "verify_local_images", return_value={"previous": self.a}),
        ):
            c.main()
        self.assertEqual(c.current(), self.a)
        self.assertEqual(self.activate.call_args.args, (self.a,))

    @contextmanager
    def replacement_inputs(self):
        self.addCleanup(os.umask, os.umask(0))
        c.atomic(self.root / "current", self.a)
        temp = self.root / "upload"
        temp.mkdir()
        for name in (
            "controller.py",
            "hk-rag-candidate-host.sh",
            "compose.yaml",
            "images.tar",
        ):
            (temp / name).write_bytes(b"fixture")
        keys = (
            "HK_PG_PASSWORD",
            "HK_REDIS_PASSWORD",
            "HK_SILO_ROOT_USER",
            "HK_SILO_ROOT_PASSWORD",
            "HK_S3_ACCESS_KEY",
            "HK_S3_SECRET_KEY",
            "HK_JWT_SECRET",
            "HK_ENCRYPTION_KEY",
            "HK_ADMIN_EMAILS",
            "HK_MODEL_CREDENTIALS_JSON",
        )
        config = "".join(f"{key}=test-value\n" for key in keys)
        (self.root / "secrets.env").write_text(config)
        (self.releases / self.a / ".env").write_text(config)
        sha = "2" * 40
        image_ids = ["sha256:" + x * 64 for x in ("a", "b")]
        with (
            patch.object(c, "HERE", temp),
            patch.object(c, "archive_check"),
            patch.object(c, "enough_capacity"),
            patch.object(c, "command", return_value=Mock(stdout="/tmp")),
            patch.object(c, "pin_infra", return_value={"HK_POSTGRES_IMAGE": "pg@verified"}),
            patch.object(c, "runtime_id", side_effect=image_ids),
            patch.object(c, "volume", side_effect=lambda p, v: p + "_" + v) as volume,
            patch.object(c, "volume_helper") as helper,
        ):
            yield (sha, "a" * 64, *image_ids), volume, helper

    def test_deploy_clones_only_checked_previous_candidate_volumes(self):
        with (
            self.replacement_inputs() as (args, volume, helper),
            patch.object(c, "check_available_memory") as check,
        ):
            def assert_quiesced():
                self.stop.assert_called_once_with(self.a)
                self.assertTrue((self.root / "pending.json").is_file())
                helper.assert_not_called()

            check.side_effect = assert_quiesced
            c.deploy(*args)
            check.assert_called_once()
        after = c.current()
        self.assertNotEqual(after, self.a)
        self.assertEqual(len(helper.call_args_list), 3)
        self.assertEqual(
            [v.args for v in volume.call_args_list],
            [(project, key) for key in c.VOLUMES for project in (self.a, after)],
        )
        for call, key in zip(helper.call_args_list, c.VOLUMES, strict=False):
            self.assertEqual(
                call.args[1],
                [
                    "-v",
                    self.a + "_" + key + ":/source:ro",
                    "-v",
                    after + "_" + key + ":/target",
                ],
            )
        self.assertEqual(stat.S_IMODE((self.releases / after).stat().st_mode), 0o700)
        for name in (
            ".env",
            "pre-migration.dump",
            "manifest.json",
            "controller.py",
            "compose.yaml",
        ):
            self.assertEqual(stat.S_IMODE((self.releases / after / name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "current").stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE((self.releases / after / "hk-rag-candidate-host.sh").stat().st_mode),
            0o700,
        )
        self.assertNotIn(
            "storage-init",
            [arg for call in self.compose.call_args_list for arg in call.args],
        )
        self.activate.assert_called_once_with(after)

    def test_replacement_refuses_low_memory_and_restores_previous_candidate(self):
        with self.replacement_inputs() as (args, volume, helper):
            previous_env = (self.releases / self.a / ".env").read_bytes()
            low_memory = "MemAvailable: 1536000 kB\nSwapFree: 4194304 kB\n"
            original_read = Path.read_text

            def read(path, *args, **kwargs):
                if path == Path("/proc/meminfo"):
                    self.stop.assert_called_once_with(self.a)
                    self.assertTrue((self.root / "pending.json").is_file())
                    return low_memory
                return original_read(path, *args, **kwargs)

            with (
                patch.object(Path, "read_text", read),
                self.assertRaisesRegex(ValueError, "1600 MiB.*measured 1500 MiB"),
            ):
                c.deploy(*args)
            self.assertEqual(c.current(), self.a)
            self.assertEqual((self.releases / self.a / ".env").read_bytes(), previous_env)
            self.assertFalse((self.root / "pending.json").exists())
            self.activate.assert_called_once_with(self.a)
            self.assertEqual(self.stop.call_count, 2)
            self.assertNotEqual(self.stop.call_args.args[0], self.a)
            volume.assert_not_called()
            helper.assert_not_called()
            self.assertFalse(
                any(x.args[1] in ("up", "run", "exec") for x in self.compose.call_args_list)
            )

    def test_preflight_checks_fresh_ram_and_preserves_replacement_cpu_and_disk_gates(self):
        with (
            patch.object(c.os, "sched_getaffinity", return_value={0, 1}) as affinity,
            patch.object(c, "check_available_memory", side_effect=ValueError("low RAM")) as check,
            patch.object(c, "verify_local_images", return_value={"infra": c.INFRA}),
            patch.object(c, "volume"),
            patch.object(c, "volume_helper", return_value=Mock(stdout="0 fixture")),
            patch.object(c.shutil, "disk_usage", return_value=Mock(free=0)),
        ):
            with self.assertRaisesRegex(ValueError, "low RAM"):
                c.enough_capacity(None, 100, self.root)
            check.assert_called_once()
            check.reset_mock()
            with self.assertRaisesRegex(ValueError, "Insufficient space"):
                c.enough_capacity(self.a, 100, self.root)
            affinity.return_value = {0}
            with self.assertRaisesRegex(ValueError, "two CPUs"):
                c.enough_capacity(self.a, 100, self.root)
            check.assert_not_called()
            self.stop.assert_not_called()

    def test_archive_rejects_retagging_and_false_commit_label(self):
        sha = "b" * 40

        def archive(tags, label):
            output = self.root / "images.tar"
            identities = {}
            with tarfile.open(output, "w") as tar:
                rows = []
                for role, tag in zip(("api", "console"), tags, strict=False):
                    config = json.dumps(
                        {
                            "config": {"Labels": {"org.opencontainers.image.revision": label}},
                            "role": role,
                            "os": "linux",
                            "architecture": "amd64",
                            "rootfs": {"type": "layers", "diff_ids": []},
                        }
                    ).encode()
                    digest = hashlib.sha256(config).hexdigest()
                    identities[role] = "sha256:" + digest
                    member = tarfile.TarInfo(digest + ".json")
                    member.size = len(config)
                    tar.addfile(member, io.BytesIO(config))
                    rows.append({"Config": member.name, "RepoTags": [tag], "Layers": []})
                body = json.dumps(rows).encode()
                index = tarfile.TarInfo("manifest.json")
                index.size = len(body)
                tar.addfile(index, io.BytesIO(body))
            return (
                output,
                sha,
                hashlib.sha256(output.read_bytes()).hexdigest(),
                identities,
            )

        tags = [f"supernova-hk-candidate/{role}:{sha}" for role in ("api", "console")]
        c.archive_check(*archive(tags, sha))
        for bad_tags, label in (([tags[0], "shared:stable"], sha), (tags, "f" * 40)):
            with self.assertRaises(ValueError):
                c.archive_check(*archive(bad_tags, label))

    def test_normalized_export_and_runtime_digest_on_classic_and_containerd(self):
        sha = "4" * 40
        tags = {role: f"supernova-hk-candidate/{role}:{sha}" for role in ("api", "console")}
        for role, tag in tags.items():
            with tarfile.open(self.root / f"{role}.tar", "w") as bundle:
                layer = b"a layer"
                cfg = json.dumps(
                    {
                        "architecture": "amd64",
                        "os": "linux",
                        "role": role,
                        "config": {"Labels": {"org.opencontainers.image.revision": sha}},
                        "rootfs": {
                            "type": "layers",
                            "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()],
                        },
                    }
                ).encode()
                c.add_bytes(bundle, "blobs/config", cfg)
                c.add_bytes(bundle, "blobs/layer", layer)
                c.add_bytes(bundle, "oci-layout", b"{}")
                c.add_bytes(bundle, "index.json", b"{}")
                c.add_bytes(
                    bundle,
                    "manifest.json",
                    json.dumps(
                        [
                            {
                                "Config": "blobs/config",
                                "RepoTags": ["docker.io/" + tag],
                                "Layers": ["blobs/layer"],
                            }
                        ]
                    ).encode(),
                )
        ids = c.merge_images(self.root, sha)
        archive = self.root / "images.tar"
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        c.archive_check(archive, sha, checksum, ids)
        with tarfile.open(archive) as bundle:
            self.assertNotIn("index.json", bundle.getnames())
            self.assertEqual(bundle.extractfile("api/0/layer.tar").read(), layer)

        expected = ids["api"]
        local = {
            "Id": expected,
            "Architecture": "amd64",
            "Os": "linux",
            "Config": {"Labels": {"org.opencontainers.image.revision": sha}},
        }
        with (
            patch.object(c, "inspect_image", return_value=local),
            patch.object(c, "command") as cli,
        ):
            self.assertEqual(c.runtime_id(tags["api"], sha, expected), expected)
            cli.assert_not_called()
            local["Id"] = "sha256:" + "9" * 64

            def save(args, stdout):
                self.assertEqual(args, ["docker", "image", "save", tags["api"]])
                stdout.write((self.root / "api.tar").read_bytes())

            cli.side_effect = save
            self.assertEqual(c.runtime_id(tags["api"], sha, expected), local["Id"])
            self.assertEqual(c.runtime_id(tags["api"], sha, expected, local["Id"]), local["Id"])
            self.assertEqual(cli.call_count, 1)
            with self.assertRaises(ValueError):
                c.runtime_id(tags["api"], sha, ids["console"])
            with self.assertRaises(ValueError):
                c.runtime_id(tags["api"], sha, expected, "sha256:" + "8" * 64)
            local["Architecture"] = "arm64"
            with self.assertRaises(ValueError):
                c.runtime_id(tags["api"], sha, expected)

        with tarfile.open(archive, "a") as bundle:
            c.add_bytes(bundle, "index.json", b"{}")
        with self.assertRaises(ValueError):
            c.archive_check(archive, sha, hashlib.sha256(archive.read_bytes()).hexdigest(), ids)

    def test_infra_reuses_shared_digest_without_retagging(self):
        digest = "sha256:" + "3" * 64

        def cli(args, **kwargs):
            self.assertNotIn("pull", args)
            return Mock(stdout="amd64" if "version" in args else "available")

        with (
            patch.object(c, "command", side_effect=cli),
            patch.object(c, "INFRA", {"HK_REDIS_IMAGE": "redis:7-alpine"}),
            patch.object(
                c,
                "inspect_image",
                return_value={"RepoDigests": ["library/redis@" + digest]},
            ),
        ):
            self.assertEqual(c.pin_infra(None), {"HK_REDIS_IMAGE": "redis@" + digest})


if __name__ == "__main__":
    unittest.main()
