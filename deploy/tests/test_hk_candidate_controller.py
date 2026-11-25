"""Exercise the controller's transaction boundaries in a disposable local directory."""

import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
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

    def test_deploy_clones_only_checked_previous_candidate_volumes(self):
        c.atomic(self.root / "current", self.a)
        temp = self.root / "upload"
        temp.mkdir()
        for name in ("controller.py", "hk-rag-candidate-host.sh", "compose.yaml", "images.tar"):
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
            patch.object(c, "inspect_image", side_effect=[{"Id": v} for v in image_ids]),
            patch.object(c, "volume", side_effect=lambda p, v: p + "_" + v) as volume,
            patch.object(c, "volume_helper") as helper,
        ):
            c.deploy(sha, "a" * 64, *image_ids)
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
                ["-v", self.a + "_" + key + ":/source:ro", "-v", after + "_" + key + ":/target"],
            )
        self.assertTrue((self.releases / after / "manifest.json").is_file())
        self.assertNotIn(
            "storage-init", [arg for call in self.compose.call_args_list for arg in call.args]
        )
        self.activate.assert_called_once_with(after)

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
                        }
                    ).encode()
                    digest = hashlib.sha256(config).hexdigest()
                    identities[role] = "sha256:" + digest
                    member = tarfile.TarInfo(digest + ".json")
                    member.size = len(config)
                    tar.addfile(member, io.BytesIO(config))
                    rows.append({"Config": member.name, "RepoTags": [tag]})
                body = json.dumps(rows).encode()
                index = tarfile.TarInfo("manifest.json")
                index.size = len(body)
                tar.addfile(index, io.BytesIO(body))
            return output, sha, hashlib.sha256(output.read_bytes()).hexdigest(), identities

        tags = [f"supernova-hk-candidate/{role}:{sha}" for role in ("api", "console")]
        c.archive_check(*archive(tags, sha))
        for bad_tags, label in (([tags[0], "shared:stable"], sha), (tags, "f" * 40)):
            with self.assertRaises(ValueError):
                c.archive_check(*archive(bad_tags, label))

    def test_infra_reuses_shared_digest_without_retagging(self):
        digest = "sha256:" + "3" * 64

        def cli(args, **kwargs):
            self.assertNotIn("pull", args)
            return Mock(stdout="amd64" if "version" in args else "available")

        with (
            patch.object(c, "command", side_effect=cli),
            patch.object(c, "INFRA", {"HK_REDIS_IMAGE": "redis:7-alpine"}),
            patch.object(
                c, "inspect_image", return_value={"RepoDigests": ["library/redis@" + digest]}
            ),
        ):
            self.assertEqual(c.pin_infra(None), {"HK_REDIS_IMAGE": "redis@" + digest})


if __name__ == "__main__":
    unittest.main()
