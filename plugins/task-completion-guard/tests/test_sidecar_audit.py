"""Black-box regression coverage for completion audits stored outside chat."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "scripts" / "completion_guard.py"


class SidecarAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.data = self.root / "plugin-data"
        self.temp_root = self.root / "task-tmp"
        self.temp_root.mkdir()
        self.env = os.environ.copy()
        self.env.update(
            PLUGIN_ROOT=str(PLUGIN_ROOT),
            PLUGIN_DATA=str(self.data),
            TMPDIR=str(self.temp_root),
        )
        self.python = os.environ.get("PYTHON", "python3")
        self.session = "sidecar-session"
        self.event_number = 0

    def command(self, args, stdin):
        return subprocess.run(
            [self.python, str(SCRIPT)] + args,
            input=stdin,
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )

    def hook(self, name, **fields):
        payload = {
            "session_id": self.session,
            "turn_id": "sidecar-turn",
            "cwd": str(self.workspace),
            "hook_event_name": name,
            "permission_mode": "default",
        }
        payload.update(fields)
        result = self.command(["hook"], json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail("Hook did not return JSON: %r / %r" % (result.stdout, result.stderr))

    def state_path(self):
        paths = list(self.data.rglob("active.json"))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def state(self):
        return json.loads(self.state_path().read_text())

    def start(self):
        output = self.hook("UserPromptSubmit", prompt="请帮我修复导出功能")
        state = self.state()
        self.assertEqual(state["declaration_protocol"], "file")
        self.assertTrue(Path(state["audit_path"]).is_absolute())
        self.assertGreater(state["audit_not_before_ns"], 0)
        return state["task_id"], output["hookSpecificOutput"]["additionalContext"]

    def changed_and_verified(self):
        self.event_number += 1
        self.hook(
            "PostToolUse",
            tool_name="apply_patch",
            tool_use_id="patch-%d" % self.event_number,
            tool_input={"command": "*** Begin Patch"},
            tool_response={"isError": False, "output": "Done!"},
        )
        self.hook(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="test-%d" % self.event_number,
            tool_input={"command": "python3 -m unittest discover -s tests"},
            tool_response={"exit_code": 0, "output": "OK"},
        )

    def declaration(self, task_id):
        return {
            "version": 1,
            "task_id": task_id,
            "status": "complete",
            "criteria": [
                {
                    "id": "C%d" % index,
                    "description": "Acceptance behavior %d is complete" % index,
                    "status": "done",
                    "evidence": "Scenario %d passed after the final edit" % index,
                }
                for index in range(1, 4)
            ],
            "remaining": [],
            "summary": "Requested export behavior fixed and verified",
            "verification": {
                "status": "passed",
                "summary": "python3 -m unittest discover -s tests passed",
            },
        }

    def submit(self, declaration):
        path = Path(self.state()["audit_path"])
        result = self.command(["submit", "--audit-file", str(path)], json.dumps(declaration))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(len(result.stdout), 250)
        self.assertNotIn('"criteria"', result.stdout)
        self.assertNotIn('"verification"', result.stdout)
        self.assertEqual(json.loads(path.read_text()), declaration)
        return path

    def report(self, task_id):
        path = self.state_path().parent / "audits" / task_id / "latest.json"
        self.assertTrue(path.is_file(), "Detailed audit report must be kept locally")
        return json.loads(path.read_text())

    def assert_short_block(self, output, task_id):
        self.assertEqual(output.get("decision"), "block", output)
        reason = output["reason"]
        self.assertLess(len(reason), 120)
        self.assertNotIn("\n", reason)
        self.assertRegex(reason, r"[\u4e00-\u9fff]")
        for hidden_detail in (task_id, "{", "}", "Observed evidence", "criteria", "verification"):
            self.assertNotIn(hidden_detail, reason)
        return reason

    def test_context_uses_local_file_without_html_marker(self):
        _, context = self.start()
        self.assertIn(self.state()["audit_path"], context)
        self.assertIn("submit", context)
        self.assertIn("--audit-file", context)
        self.assertNotIn("<!--", context)
        self.assertNotIn("append exactly one hidden marker", context)

    def test_valid_local_audit_allows_natural_language_final(self):
        task_id, _ = self.start()
        self.changed_and_verified()
        self.submit(self.declaration(task_id))
        output = self.hook("Stop", last_assistant_message="已修复，测试通过。")
        self.assertEqual(output, {})
        self.assertEqual(self.state()["phase"], "completed")

    def test_missing_audit_has_short_reason_and_private_diagnostics(self):
        task_id, _ = self.start()
        self.changed_and_verified()
        output = self.hook("Stop", last_assistant_message="已修复。")
        reason = self.assert_short_block(output, task_id)
        report = self.report(task_id)
        self.assertTrue(report["errors"])
        self.assertRegex(" ".join(report["errors"]).lower(), "missing|not found|does not exist")
        continuation = self.hook("UserPromptSubmit", prompt=reason)
        context = continuation["hookSpecificOutput"]["additionalContext"]
        self.assertIn(task_id, context)
        self.assertTrue(any(error in context for error in report["errors"]))

    def test_audit_written_before_later_mutation_is_rejected(self):
        task_id, _ = self.start()
        self.changed_and_verified()
        self.submit(self.declaration(task_id))
        self.changed_and_verified()
        output = self.hook("Stop", last_assistant_message="已修复，测试通过。")
        self.assert_short_block(output, task_id)
        self.assertRegex(" ".join(self.report(task_id)["errors"]).lower(), "stale|older|before")

    def test_real_user_addendum_invalidates_existing_audit(self):
        task_id, _ = self.start()
        self.changed_and_verified()
        self.submit(self.declaration(task_id))
        before = self.state()["audit_not_before_ns"]
        self.hook("UserPromptSubmit", prompt="也请处理空文件导出")
        self.assertGreater(self.state()["audit_not_before_ns"], before)
        output = self.hook("Stop", last_assistant_message="已完成所有调整。")
        self.assert_short_block(output, task_id)
        self.assertRegex(" ".join(self.report(task_id)["errors"]).lower(), "stale|older|before")

    def test_own_continuation_does_not_invalidate_fresh_audit(self):
        task_id, _ = self.start()
        self.changed_and_verified()
        stop = self.hook("Stop", last_assistant_message="已修复。")
        self.assert_short_block(stop, task_id)
        self.submit(self.declaration(task_id))
        before = self.state()["audit_not_before_ns"]
        self.hook("UserPromptSubmit", prompt=stop["reason"])
        self.assertEqual(self.state()["audit_not_before_ns"], before)
        output = self.hook("Stop", last_assistant_message="已修复，测试通过。")
        self.assertEqual(output, {})

    def test_wrong_task_and_invalid_schema_audits_are_rejected(self):
        for index, patch in enumerate((
            {"task_id": "tcg_wrongtask"},
            {"version": 999},
            {"criteria": "not an array"},
        )):
            with self.subTest(patch=patch):
                self.session = "invalid-audit-%d" % index
                task_id, _ = self.start()
                self.changed_and_verified()
                declaration = self.declaration(task_id)
                declaration.update(patch)
                path = Path(self.state()["audit_path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(declaration))
                output = self.hook("Stop", last_assistant_message="已修复。")
                self.assert_short_block(output, task_id)
                self.assertTrue(self.report(task_id)["errors"])
                # The harness expects one state path at a time.
                self.state_path().unlink()

    def test_invalid_submit_preserves_previous_audit_bytes(self):
        task_id, _ = self.start()
        self.changed_and_verified()
        path = self.submit(self.declaration(task_id))
        before = path.read_bytes()
        wrong_task = self.declaration("tcg_000000000000")
        wrong_version = self.declaration(task_id)
        wrong_version["version"] = 999
        for invalid in (
            '{"version":',
            "[]",
            '"not an object"',
            json.dumps(wrong_task),
            json.dumps(wrong_version),
            " " * (64 * 1024 + 1),
        ):
            with self.subTest(invalid=invalid):
                result = self.command(["submit", "--audit-file", str(path)], invalid)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_bytes(), before)

    def test_submit_tool_event_does_not_invalidate_its_audit(self):
        task_id, _ = self.start()
        self.changed_and_verified()
        path = self.submit(self.declaration(task_id))
        self.hook(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="submit-audit",
            tool_input={
                "command": "python3 %s submit --audit-file %s" % (SCRIPT, path),
            },
            tool_response={"exit_code": 0, "output": "Audit submitted."},
        )
        output = self.hook("Stop", last_assistant_message="已修复，测试通过。")
        self.assertEqual(output, {})

    def test_existing_active_state_is_migrated_on_post_tool_use(self):
        task_id, _ = self.start()
        path = self.state_path()
        state = self.state()
        for key in ("declaration_protocol", "audit_path", "audit_not_before_ns"):
            state.pop(key)
        path.write_text(json.dumps(state))
        output = self.hook(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="legacy-state-inspection",
            tool_input={"command": "git status --short"},
            tool_response={"exit_code": 0, "output": ""},
        )
        migrated = self.state()
        self.assertEqual(migrated["task_id"], task_id)
        self.assertEqual(migrated["declaration_protocol"], "file")
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn(migrated["audit_path"], context)
        self.assertNotIn("<!--", context)


if __name__ == "__main__":
    unittest.main()
