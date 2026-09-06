"""Exercise review snapshots against real, isolated Git worktrees."""

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "review_snapshot.py"
SPEC = importlib.util.spec_from_file_location("review_snapshot", MODULE)
SNAPSHOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SNAPSHOT)
capture_snapshot = SNAPSHOT.capture_snapshot
check_snapshot = SNAPSHOT.check_snapshot
SnapshotError = SNAPSHOT.SnapshotError


class ReviewSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="review-snapshot-test-")
        self.base = Path(self.temporary.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.write("tracked.py", "first = 1\nsecond = 2\n")
        self.write("remove.txt", "remove one\nremove two\n")
        self.write("rename.txt", "rename this\n")
        self.write(".gitignore", "ignored.txt\n")
        self.git("add", ".")
        self.commit("Initial fixture")

    def tearDown(self):
        # Snapshot artifacts intentionally have no write bits.
        for directory, _, files in os.walk(str(self.base)):
            Path(directory).chmod(0o700)
            for name in files:
                path = Path(directory) / name
                if not path.is_symlink():
                    path.chmod(0o600)
        self.temporary.cleanup()

    def git(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.repo)] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=2,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        )
        return result.stdout

    def commit(self, message, *args):
        self.git("-c", "user.name=Snapshot Test", "-c", "user.email=snapshot@example.invalid", "commit", "-q", "-m", message, *args)

    def write(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def test_merges_explicit_dirty_and_untracked_and_excludes_ignored(self):
        self.write("tracked.py", "first = 7\nsecond = 2\n")
        self.write("nested/new.ts", "const a = 1;\nconst b = 2;\n")
        self.write("ignored.txt", "outside implicit scope\n")
        snapshot = capture_snapshot(self.repo, paths=["remove.txt"])
        self.assertEqual(snapshot["root"], str(self.repo))
        self.assertEqual(snapshot["paths"], ["nested/new.ts", "remove.txt", "tracked.py"])
        self.assertEqual(snapshot["capture_paths"], ["remove.txt"])
        self.assertEqual(snapshot["file_count"], 3)
        self.assertEqual(snapshot["code_file_count"], 2)
        self.assertEqual(snapshot["changed_lines"], 4)
        self.assertEqual(snapshot["head"], self.git("rev-parse", "HEAD").decode().strip())
        self.assertTrue(check_snapshot(snapshot))
        self.assertEqual(snapshot["digest"], capture_snapshot(self.repo, ["remove.txt", "remove.txt"])["digest"])

    def test_deletion_is_recorded_and_counted(self):
        (self.repo / "remove.txt").unlink()
        destination = self.base / "review"
        snapshot = capture_snapshot(self.repo, destination=destination)
        self.assertEqual(snapshot["paths"], ["remove.txt"])
        self.assertEqual(snapshot["entries"]["remove.txt"], {"type": "missing", "sha256": None, "size": 0, "mode": None})
        self.assertEqual(snapshot["file_count"], 1)
        self.assertEqual(snapshot["changed_lines"], 2)
        self.assertFalse((destination / "files/remove.txt").exists())
        self.assertIn(b"deleted file mode", (destination / "diff.patch").read_bytes())
        self.assertTrue(check_snapshot(snapshot))

    def test_rename_captures_both_old_and_new_paths(self):
        self.git("mv", "rename.txt", "renamed.txt")
        snapshot = capture_snapshot(self.repo)
        self.assertEqual(snapshot["paths"], ["rename.txt", "renamed.txt"])
        self.assertEqual(snapshot["entries"]["rename.txt"]["type"], "missing")
        self.assertEqual(snapshot["entries"]["renamed.txt"]["type"], "file")
        self.assertTrue(check_snapshot(snapshot))

    def test_content_change_invalidates_existing_snapshot(self):
        self.write("tracked.py", "changed = 1\n")
        snapshot = capture_snapshot(self.repo)
        self.write("tracked.py", "changed = 2\n")
        self.assertFalse(check_snapshot(snapshot))

    def test_new_dirty_file_is_detected_even_with_explicit_scope(self):
        snapshot = capture_snapshot(self.repo, paths=["tracked.py"])
        self.write("remove.txt", "new dirty file\n")
        self.assertFalse(check_snapshot(snapshot))

    def test_new_untracked_file_invalidates_snapshot(self):
        snapshot = capture_snapshot(self.repo)
        self.write("new.txt", "new file\n")
        self.assertFalse(check_snapshot(snapshot))

    def test_deleted_untracked_file_invalidates_snapshot(self):
        path = self.write("new.txt", "new file\n")
        snapshot = capture_snapshot(self.repo)
        path.unlink()
        self.assertFalse(check_snapshot(snapshot))

    def test_new_commit_invalidates_even_when_file_bytes_stay_same(self):
        snapshot = capture_snapshot(self.repo, paths=["tracked.py"])
        self.commit("Only HEAD changes", "--allow-empty")
        self.assertFalse(check_snapshot(snapshot))

    def test_file_mode_change_invalidates_snapshot(self):
        path = self.repo / "tracked.py"
        snapshot = capture_snapshot(self.repo, paths=["tracked.py"])
        path.chmod(path.stat().st_mode ^ stat.S_IXUSR)
        self.assertFalse(check_snapshot(snapshot))

    def test_saved_copy_is_stable_read_only_and_keeps_hierarchy(self):
        self.write("nested/deeper/new.py", "saved = True\n")
        self.write("tracked.py", "changed = True\n")
        index_before = (self.repo / ".git/index").read_bytes()
        destination = self.base / "artifacts"
        snapshot = capture_snapshot(self.repo, destination=destination)
        saved = destination / "files/nested/deeper/new.py"
        self.assertEqual(saved.read_text(), "saved = True\n")
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o500)
        self.assertEqual(json.loads((destination / "entries.json").read_text()), snapshot)
        self.assertIn(b"changed = True", (destination / "diff.patch").read_bytes())
        self.assertEqual((self.repo / ".git/index").read_bytes(), index_before)
        self.assertNotIn(str(self.repo).encode(), (destination / "diff.patch").read_bytes())
        self.write("nested/deeper/new.py", "saved = False\n")
        self.assertEqual(saved.read_text(), "saved = True\n")
        self.assertFalse(check_snapshot(snapshot))

    def test_digest_does_not_depend_on_destination(self):
        self.write("new.py", "x = 1\n")
        plain = capture_snapshot(self.repo)
        saved = capture_snapshot(self.repo, destination=self.base / "saved")
        self.assertEqual(plain["digest"], saved["digest"])

    def test_nested_cwd_promotes_root_and_resolves_explicit_paths(self):
        self.write("nested/file.py", "x = 1\n")
        snapshot = capture_snapshot(self.repo / "nested", paths=["file.py"])
        self.assertEqual(snapshot["root"], str(self.repo))
        self.assertEqual(snapshot["capture_paths"], ["nested/file.py"])
        self.assertTrue(check_snapshot(snapshot))

    def test_literal_git_pathspec_does_not_expand_wildcards(self):
        self.write("star[1].py", "x = 1\n")
        self.write("star1.py", "x = 2\n")
        self.git("add", ".")
        self.commit("Literal path fixture")
        self.write("star[1].py", "x = 3\n")
        snapshot = capture_snapshot(self.repo, paths=["star[1].py"])
        self.assertEqual(snapshot["paths"], ["star[1].py"])
        self.assertEqual(snapshot["changed_lines"], 2)

    def test_explicit_ignored_file_is_included(self):
        self.write("ignored.txt", "explicit content\n")
        snapshot = capture_snapshot(self.repo, paths=["ignored.txt"])
        self.assertEqual(snapshot["paths"], ["ignored.txt"])
        self.assertEqual(snapshot["changed_lines"], 1)
        self.write("ignored.txt", "changed explicit content\n")
        self.assertFalse(check_snapshot(snapshot))

    def test_non_git_directory_requires_and_tracks_explicit_files(self):
        plain = self.base / "plain"
        plain.mkdir()
        (plain / "app.py").write_text("x = 1\nx = 2\n")
        with self.assertRaises(SnapshotError):
            capture_snapshot(plain)
        snapshot = capture_snapshot(plain, ["app.py", "deleted.txt"])
        self.assertFalse(snapshot["is_git"])
        self.assertIsNone(snapshot["head"])
        self.assertEqual(snapshot["changed_lines"], 2)
        self.assertEqual(snapshot["entries"]["deleted.txt"]["type"], "missing")
        self.assertTrue(check_snapshot(snapshot))
        (plain / "deleted.txt").write_text("appeared\n")
        self.assertFalse(check_snapshot(snapshot))

    def test_unborn_repository_supports_staged_and_untracked_files(self):
        self.repo = self.base / "unborn"
        self.repo.mkdir()
        self.git("init", "-q")
        self.write("staged.py", "a = 1\n")
        self.git("add", "staged.py")
        self.write("untracked.py", "b = 2\n")
        snapshot = capture_snapshot(self.repo)
        self.assertIsNone(snapshot["head"])
        self.assertEqual(snapshot["paths"], ["staged.py", "untracked.py"])
        self.assertEqual(snapshot["changed_lines"], 2)
        self.assertTrue(check_snapshot(snapshot))

    def test_critical_paths_flag_and_code_file_count(self):
        self.write("src/auth/permission.ts", "export const allowed = true;\n")
        self.write("readme.md", "Notes\n")
        snapshot = capture_snapshot(self.repo)
        self.assertTrue(snapshot["critical"])
        self.assertEqual(snapshot["code_file_count"], 1)

    def test_rejects_path_escape_absolute_and_directory(self):
        for path in ("../outside", "nested/../../outside", str(self.base / "outside"), ".", ".git"):
            with self.subTest(path=path), self.assertRaises(SnapshotError):
                capture_snapshot(self.repo, paths=[path])

    def test_rejects_symlink_files_and_parent_components(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("not for review\n")
        (self.repo / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(SnapshotError):
            capture_snapshot(self.repo, ["link/secret.txt"])
        (self.repo / "link").unlink()
        (self.repo / "link.txt").symlink_to(outside / "secret.txt")
        with self.assertRaises(SnapshotError):
            capture_snapshot(self.repo)

    def test_symlink_introduced_after_capture_cannot_pass_check(self):
        snapshot = capture_snapshot(self.repo, ["tracked.py"])
        (self.repo / "tracked.py").unlink()
        (self.repo / "tracked.py").symlink_to(self.repo / "remove.txt")
        with self.assertRaises(SnapshotError):
            check_snapshot(snapshot)

    def test_rejects_special_file_without_blocking(self):
        os.mkfifo(self.repo / "pipe")
        with self.assertRaises(SnapshotError):
            capture_snapshot(self.repo, ["pipe"])

    def test_file_count_limit_fails_instead_of_truncating(self):
        for index in range(SNAPSHOT.MAX_FILES + 1):
            self.write("many/file-{}.txt".format(index), "x\n")
        with self.assertRaisesRegex(SnapshotError, "200 file"):
            capture_snapshot(self.repo)

    def test_single_file_limit_fails_before_artifacts_are_created(self):
        self.write("large.bin", b"x" * (SNAPSHOT.MAX_FILE_BYTES + 1))
        destination = self.base / "review"
        with self.assertRaisesRegex(SnapshotError, "2 MiB"):
            capture_snapshot(self.repo, destination=destination)
        self.assertFalse(destination.exists())

    def test_total_content_limit_is_enforced(self):
        for index in range(5):
            self.write("large-{}.bin".format(index), b"\x00" * SNAPSHOT.MAX_FILE_BYTES)
        with self.assertRaisesRegex(SnapshotError, "8 MiB"):
            capture_snapshot(self.repo)

    def test_diff_limit_is_enforced(self):
        self.write("tracked.py", "replaced = True\n")
        with mock.patch.object(SNAPSHOT, "MAX_DIFF_BYTES", 16):
            with self.assertRaisesRegex(SnapshotError, "output exceeded"):
                capture_snapshot(self.repo)

    def test_binary_file_is_hashed_without_binary_patch_expansion(self):
        self.write("image.bin", b"\x00before")
        self.git("add", "image.bin")
        self.commit("Binary fixture")
        self.write("image.bin", b"\x00after")
        destination = self.base / "review"
        snapshot = capture_snapshot(self.repo, destination=destination)
        self.assertEqual(snapshot["entries"]["image.bin"]["size"], 6)
        self.assertIn(b"Binary files", (destination / "diff.patch").read_bytes())
        self.assertNotIn(b"GIT binary patch", (destination / "diff.patch").read_bytes())
        self.assertTrue(check_snapshot(snapshot))

    def test_destination_cannot_modify_worktree_or_overwrite_existing_data(self):
        self.write("new.txt", "new\n")
        for target in (self.repo / "review", self.repo):
            with self.assertRaises(SnapshotError):
                capture_snapshot(self.repo, destination=target)
        target = self.base / "review"
        target.mkdir()
        (target / "precious.txt").write_text("keep\n")
        with self.assertRaises(SnapshotError):
            capture_snapshot(self.repo, destination=target)
        self.assertEqual((target / "precious.txt").read_text(), "keep\n")

    def test_git_timeout_is_reported_and_process_is_killed(self):
        fake_process = mock.Mock()
        fake_process.wait.side_effect = [subprocess.TimeoutExpired("git", 2), 0]
        with mock.patch.object(SNAPSHOT.subprocess, "Popen", return_value=fake_process):
            with self.assertRaisesRegex(SnapshotError, "time limit"):
                capture_snapshot(self.repo)
        fake_process.kill.assert_called_once_with()
        self.assertEqual(fake_process.wait.call_count, 2)

    def test_capture_detects_concurrent_scope_change(self):
        original = SNAPSHOT._patch_and_lines

        def change_after_patch(*args):
            result = original(*args)
            self.write("late.py", "late = True\n")
            return result

        with mock.patch.object(SNAPSHOT, "_patch_and_lines", side_effect=change_after_patch):
            with self.assertRaisesRegex(SnapshotError, "changed while capturing"):
                capture_snapshot(self.repo, ["tracked.py"])

    def test_inconsistent_metadata_cannot_pass_verification(self):
        snapshot = capture_snapshot(self.repo, ["tracked.py"])
        snapshot["entries"]["tracked.py"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(SnapshotError, "inconsistent"):
            check_snapshot(snapshot)

    def test_change_metrics_are_bound_to_the_snapshot_digest(self):
        snapshot = capture_snapshot(self.repo, ["tracked.py"])
        snapshot["code_file_count"] = 0
        with self.assertRaisesRegex(SnapshotError, "inconsistent"):
            check_snapshot(snapshot)

    def test_git_clean_filters_cannot_execute_or_mutate_the_source(self):
        self.write(".gitattributes", "tracked.py filter=sideeffect\n")
        self.git("config", "filter.sideeffect.clean", "touch changed-by-filter; cat")
        self.git("config", "filter.sideeffect.required", "true")
        self.write("tracked.py", "changed = True\n")
        snapshot = capture_snapshot(self.repo)
        self.assertFalse((self.repo / "changed-by-filter").exists())
        self.assertEqual(snapshot["paths"], [".gitattributes", "tracked.py"])
        self.assertTrue(check_snapshot(snapshot))
        self.assertFalse((self.repo / "changed-by-filter").exists())


if __name__ == "__main__":
    unittest.main()
