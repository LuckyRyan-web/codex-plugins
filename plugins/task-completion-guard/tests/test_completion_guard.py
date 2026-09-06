import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "scripts" / "completion_guard.py"
HOOKS = PLUGIN_ROOT / "hooks" / "hooks.json"
MANIFEST = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
SKILL = PLUGIN_ROOT / "skills" / "task-completion-guard" / "SKILL.md"


class CompletionGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "plugin-data"
        self.cwd = self.root / "workspace"
        self.cwd.mkdir()
        self.session = "session-a"
        self.turn = "turn-a"

    def run_hook(self, event_name, **fields):
        payload = {
            "session_id": self.session,
            "turn_id": self.turn,
            "cwd": str(self.cwd),
            "hook_event_name": event_name,
            "model": "test-model",
            "permission_mode": "default",
        }
        # Legacy test fixtures describe declarations inline; deliver them via
        # the new file channel and keep the actual final answer plain text.
        if event_name == "Stop" and isinstance(fields.get("last_assistant_message"), str):
            match = re.search(r"<!-- task-completion-guard:(.*) -->", fields["last_assistant_message"])
            if match:
                state = self.read_state()
                Path(state["audit_path"]).write_text(match.group(1))
                fields["last_assistant_message"] = "Finished."
        payload.update(fields)
        env = os.environ.copy()
        env["PLUGIN_DATA"] = str(self.data)
        env["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        completed = subprocess.run(
            [os.environ.get("PYTHON", "python3"), str(SCRIPT), "hook"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.strip(), "hook must emit valid JSON")
        try:
            return json.loads(completed.stdout), completed
        except json.JSONDecodeError as exc:
            self.fail("invalid hook JSON: %s\n%s" % (exc, completed.stdout))

    def start_task(self, prompt="请帮我实现订单批量导出功能"):
        output, _ = self.run_hook("UserPromptSubmit", prompt=prompt)
        context = output["hookSpecificOutput"]["additionalContext"]
        match = re.search(r"task (tcg_[0-9a-f]+)", context)
        self.assertIsNotNone(match, context)
        return match.group(1), context

    def use_legacy_active_task(self):
        # Existing tasks must retain the pre-independent-review completion
        # contract when a plugin is upgraded in the middle of their work.
        state = self.read_state()
        state.pop("review", None)
        self.active_state_path().write_text(json.dumps(state))

    def active_state_path(self):
        paths = list(self.data.rglob("active.json"))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def read_state(self):
        return json.loads(self.active_state_path().read_text())

    def audit_errors(self):
        state = self.read_state()
        report = self.active_state_path().parent / "audits" / state["task_id"] / "latest.json"
        return "\n".join(json.loads(report.read_text())["errors"])

    def criteria_for(self, count):
        return [
            {
                "id": "C%d" % (index + 1),
                "description": "acceptance item %d" % (index + 1),
                "status": "done",
                "evidence": "verified evidence %d" % (index + 1),
            }
            for index in range(count)
        ]

    def complete_marker(self, task_id, count=3, verification="passed", **extra):
        marker = {
            "version": 1,
            "task_id": task_id,
            "status": "complete",
            "criteria": self.criteria_for(count),
            "remaining": [],
            "summary": "implemented and verified the complete request",
            "verification": {
                "status": verification,
                "summary": "pnpm test passed",
            },
        }
        if verification == "not_applicable":
            marker["verification"] = {
                "status": "not_applicable",
                "reason": "No executable validation applies to this metadata-only result.",
            }
        marker.update(extra)
        return "Finished.\n<!-- task-completion-guard:%s -->" % json.dumps(
            marker, separators=(",", ":")
        )

    def post_patch(self, event_id="patch-1", success=True):
        response = {"isError": not success, "output": "Done!" if success else "patch failed"}
        return self.run_hook(
            "PostToolUse",
            tool_name="apply_patch",
            tool_use_id=event_id,
            tool_input={"command": "*** Begin Patch"},
            tool_response=response,
        )[0]

    def post_verification(self, event_id="test-1", exit_code=0):
        return self.run_hook(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id=event_id,
            tool_input={"command": "pnpm test"},
            tool_response={"exit_code": exit_code, "output": "tests complete"},
        )[0]

    def test_information_question_is_not_enrolled(self):
        output, _ = self.run_hook("UserPromptSubmit", prompt="如何修复这个错误？")
        self.assertEqual(output, {})
        stop, _ = self.run_hook("Stop", last_assistant_message="Use the documented API.")
        self.assertEqual(stop, {})
        self.assertEqual(list(self.data.rglob("active.json")), [])

    def test_explicit_read_only_request_is_exempt(self):
        output, _ = self.run_hook(
            "UserPromptSubmit", prompt="请只分析这个错误，不要修改代码"
        )
        self.assertEqual(output, {})
        self.assertEqual(list(self.data.rglob("active.json")), [])

    def test_plan_permission_mode_is_exempt(self):
        output, _ = self.run_hook(
            "UserPromptSubmit", prompt="请帮我实现登录功能", permission_mode="plan"
        )
        self.assertEqual(output, {})

    def test_plan_only_natural_language_requests_are_exempt(self):
        prompts = ["帮我写一个重构方案", "Draft a migration plan."]
        for index, prompt in enumerate(prompts):
            with self.subTest(prompt=prompt):
                self.session = "plan-only-%d" % index
                output, _ = self.run_hook("UserPromptSubmit", prompt=prompt)
                self.assertEqual(output, {})

    def test_read_only_steer_suspends_active_guard(self):
        self.start_task()
        output, _ = self.run_hook(
            "UserPromptSubmit", prompt="先只分析这个问题，不要修改代码"
        )
        self.assertEqual(output, {})
        self.assertEqual(self.read_state()["phase"], "suspended")
        stop, _ = self.run_hook("Stop", last_assistant_message="Analysis only.")
        self.assertEqual(stop, {})

    def test_unknown_bash_mutation_rearms_a_suspended_guard(self):
        self.start_task()
        self.run_hook(
            "UserPromptSubmit", prompt="先只分析这个问题，不要修改代码"
        )
        output, _ = self.run_hook(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="suspended-custom-rewrite",
            tool_input={"command": "python scripts/rewrite_config.py"},
            tool_response={"exit_code": 0, "output": "rewrote configuration"},
        )
        self.assertIn("hookSpecificOutput", output)
        self.assertEqual(self.read_state()["phase"], "active")
        events = list(self.data.rglob("events/**/*.json"))
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0].read_text())["kind"], "possible_mutation")

    def test_chinese_resume_phrase_restores_suspended_guard(self):
        task_id, _ = self.start_task()
        self.run_hook("Interrupt")
        output, _ = self.run_hook("UserPromptSubmit", prompt="继续刚才的任务")
        self.assertIn(task_id, output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.read_state()["phase"], "active")

    def test_short_natural_language_action_is_enrolled(self):
        task_id, context = self.start_task("那你写吧")
        self.assertIn(task_id, context)
        self.assertEqual(self.read_state()["phase"], "active")

    def test_realistic_fix_requests_are_enrolled(self):
        prompts = [
            "看一下这个登录问题，处理一下",
            "排查一下导出失败并修掉",
            "这个 bug 帮我搞定",
            "Look into this login bug and fix it",
            "Investigate the failing export and resolve it",
        ]
        for index, prompt in enumerate(prompts):
            with self.subTest(prompt=prompt):
                self.session = "realistic-fix-%d" % index
                output, _ = self.run_hook("UserPromptSubmit", prompt=prompt)
                self.assertIn("hookSpecificOutput", output)

    def test_missing_marker_blocks_stop(self):
        task_id, _ = self.start_task()
        output, _ = self.run_hook("Stop", last_assistant_message="I changed one file.")
        self.assertEqual(output["decision"], "block")
        self.assertLess(len(output["reason"]), 80)
        self.assertIn("local completion audit is missing", self.audit_errors())

    def test_continuation_prompt_preserves_task(self):
        task_id, _ = self.start_task()
        stop, _ = self.run_hook("Stop", last_assistant_message="Partial result")
        before = self.read_state()
        continuation, _ = self.run_hook("UserPromptSubmit", prompt=stop["reason"])
        after = self.read_state()
        self.assertEqual(before["task_id"], after["task_id"])
        self.assertEqual(after["task_id"], task_id)
        self.assertIn("continuation", continuation["hookSpecificOutput"]["additionalContext"])

    def test_successful_change_and_fresh_verification_allow_completion(self):
        task_id, _ = self.start_task()
        self.use_legacy_active_task()
        self.post_patch()
        self.post_verification()
        message = self.complete_marker(task_id)
        output, _ = self.run_hook("Stop", last_assistant_message=message)
        self.assertEqual(output, {})
        self.assertEqual(self.read_state()["phase"], "completed")

    def test_stale_verification_is_rejected(self):
        task_id, _ = self.start_task()
        self.post_verification()
        self.post_patch()
        message = self.complete_marker(task_id)
        output, _ = self.run_hook("Stop", last_assistant_message=message)
        self.assertEqual(output["decision"], "block")
        self.assertIn("after the final mutation", self.audit_errors())

    def test_unknown_bash_after_verification_is_a_possible_mutation(self):
        task_id, _ = self.start_task()
        self.post_patch()
        self.post_verification()
        self.run_hook(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="custom-rewrite",
            tool_input={"command": "python scripts/rewrite_config.py"},
            tool_response={"exit_code": 0, "output": "rewrote configuration"},
        )
        output, _ = self.run_hook(
            "Stop", last_assistant_message=self.complete_marker(task_id)
        )
        self.assertEqual(output["decision"], "block")
        self.assertIn("after the final mutation", self.audit_errors())

    def test_failed_unknown_bash_also_invalidates_earlier_verification(self):
        task_id, _ = self.start_task()
        self.post_patch()
        self.post_verification()
        self.run_hook(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="failed-custom-rewrite",
            tool_input={"command": "python scripts/rewrite_config.py"},
            tool_response={
                "exit_code": 1,
                "output": "rewrote files before failing",
            },
        )
        output, _ = self.run_hook(
            "Stop", last_assistant_message=self.complete_marker(task_id)
        )
        self.assertEqual(output["decision"], "block")
        self.assertIn("after the final mutation", self.audit_errors())

    def test_failed_latest_verification_is_rejected(self):
        task_id, _ = self.start_task()
        self.post_patch()
        self.post_verification("test-pass", 0)
        self.post_verification("test-fail", 1)
        output, _ = self.run_hook(
            "Stop", last_assistant_message=self.complete_marker(task_id)
        )
        self.assertEqual(output["decision"], "block")
        self.assertIn("failed after the final mutation", self.audit_errors())

    def test_too_few_criteria_are_rejected(self):
        task_id, _ = self.start_task()
        self.post_patch()
        self.post_verification()
        output, _ = self.run_hook(
            "Stop", last_assistant_message=self.complete_marker(task_id, count=1)
        )
        self.assertEqual(output["decision"], "block")
        self.assertIn("at least 3 meaningful criteria", self.audit_errors())

    def test_no_change_requires_specific_reason(self):
        task_id, _ = self.start_task("请修改这个配置")
        self.use_legacy_active_task()
        self.post_verification()
        output, _ = self.run_hook(
            "Stop", last_assistant_message=self.complete_marker(task_id, count=1)
        )
        self.assertEqual(output["decision"], "block")
        allowed, _ = self.run_hook(
            "Stop",
            last_assistant_message=self.complete_marker(
                task_id,
                count=1,
                no_change_reason="The requested value was already present and required no edit.",
            ),
        )
        self.assertEqual(allowed, {})

    def test_not_applicable_verification_requires_reason_but_can_pass(self):
        task_id, _ = self.start_task("请修改这个配置")
        self.use_legacy_active_task()
        self.post_patch()
        output, _ = self.run_hook(
            "Stop",
            last_assistant_message=self.complete_marker(
                task_id, count=1, verification="not_applicable"
            ),
        )
        self.assertEqual(output, {})

    def test_not_applicable_cannot_hide_a_failed_verification(self):
        task_id, _ = self.start_task("请修改这个配置")
        self.post_patch()
        self.post_verification("failed-test", 1)
        output, _ = self.run_hook(
            "Stop",
            last_assistant_message=self.complete_marker(
                task_id, count=1, verification="not_applicable"
            ),
        )
        self.assertEqual(output["decision"], "block")
        self.assertIn("failed after the final mutation", self.audit_errors())

    def test_needs_user_disposition_allows_question(self):
        task_id, _ = self.start_task()
        marker = {
            "version": 1,
            "task_id": task_id,
            "status": "needs_user",
            "question": "Which authentication provider should be used?",
            "why_required": "The choice changes the public API and stored credentials.",
            "pending_criteria": ["C2"],
        }
        message = "I need a decision.\n<!-- task-completion-guard:%s -->" % json.dumps(marker)
        output, _ = self.run_hook("Stop", last_assistant_message=message)
        self.assertEqual(output, {})
        self.assertEqual(self.read_state()["phase"], "waiting_user")

    def test_blocked_disposition_requires_observed_failure(self):
        task_id, _ = self.start_task()
        marker = {
            "version": 1,
            "task_id": task_id,
            "status": "blocked",
            "reason": "The required external build service is unavailable.",
        }
        message = "Blocked.\n<!-- task-completion-guard:%s -->" % json.dumps(marker)
        denied, _ = self.run_hook("Stop", last_assistant_message=message)
        self.assertEqual(denied["decision"], "block")
        self.post_verification("failed-service", 1)
        allowed, _ = self.run_hook("Stop", last_assistant_message=message)
        self.assertEqual(allowed, {})
        self.assertEqual(self.read_state()["phase"], "blocked_external")

    def test_explicit_stop_cancels_guard(self):
        self.start_task()
        output, _ = self.run_hook("UserPromptSubmit", prompt="先停一下")
        self.assertEqual(output, {})
        stop, _ = self.run_hook("Stop", last_assistant_message="Stopped as requested.")
        self.assertEqual(stop, {})
        self.assertEqual(self.read_state()["phase"], "cancelled")

    def test_interrupt_suspends_guard(self):
        self.start_task()
        output, _ = self.run_hook("Interrupt")
        self.assertEqual(output, {})
        stop, _ = self.run_hook("Stop", last_assistant_message="Interrupted")
        self.assertEqual(stop, {})
        self.assertEqual(self.read_state()["phase"], "suspended")

    def test_mutation_auto_enrolls_unclassified_task(self):
        output, _ = self.run_hook("UserPromptSubmit", prompt="看看这里")
        self.assertEqual(output, {})
        post = self.post_patch()
        self.assertIn("hookSpecificOutput", post)
        self.assertEqual(self.read_state()["activation_source"], "mutation_observed")

    def test_read_only_shell_does_not_auto_enroll(self):
        output, _ = self.run_hook("UserPromptSubmit", prompt="看看这里")
        self.assertEqual(output, {})
        post, _ = self.run_hook(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="read-1",
            tool_input={"command": "rg TODO ."},
            tool_response={"exit_code": 0, "output": "none"},
        )
        self.assertEqual(post, {})
        self.assertEqual(list(self.data.rglob("active.json")), [])

    def test_duplicate_tool_event_is_idempotent(self):
        self.start_task()
        self.post_patch("same-event")
        self.post_patch("same-event")
        events = list(self.data.rglob("events/**/*.json"))
        self.assertEqual(len(events), 1)

    def test_state_never_stores_raw_prompt_or_tool_data(self):
        secret = "secret-value-that-must-not-be-persisted"
        self.start_task("请修改配置，令牌是 " + secret)
        self.run_hook(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="secret-command",
            tool_input={"command": "TOKEN=%s pnpm test" % secret},
            tool_response={"exit_code": 0, "output": "echoed %s" % secret},
        )
        for path in self.data.rglob("*.json"):
            self.assertNotIn(secret, path.read_text())

    def test_repeated_no_progress_stops_fail_open(self):
        self.start_task()
        first, _ = self.run_hook("Stop", last_assistant_message="partial")
        second, _ = self.run_hook(
            "Stop", last_assistant_message="partial", stop_hook_active=True
        )
        third, _ = self.run_hook(
            "Stop", last_assistant_message="partial", stop_hook_active=True
        )
        self.assertEqual(first["decision"], "block")
        self.assertEqual(second["decision"], "block")
        self.assertIn("systemMessage", third)
        self.assertIn("完成检查暂时不可用", third["systemMessage"])
        self.assertEqual(self.read_state()["phase"], "degraded")

    def test_stop_hook_active_turn_is_still_audited(self):
        self.start_task()
        output, _ = self.run_hook(
            "Stop", last_assistant_message="partial", stop_hook_active=True
        )
        self.assertEqual(output["decision"], "block")
        self.assertIn("local completion audit is missing", self.audit_errors())
        self.assertEqual(self.read_state()["phase"], "active")

    def test_corrupt_state_fails_open(self):
        self.start_task()
        self.active_state_path().write_text("not json")
        output, _ = self.run_hook("Stop", last_assistant_message="partial")
        self.assertIn("systemMessage", output)
        self.assertIn("完成检查暂时不可用", output["systemMessage"])


class PackageContractTests(unittest.TestCase):
    def test_hooks_use_canonical_plugin_layout_and_events(self):
        hooks = json.loads(HOOKS.read_text())
        self.assertEqual(
            set(hooks["hooks"]),
            {"UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "Stop", "Interrupt"},
        )
        for entries in hooks["hooks"].values():
            for entry in entries:
                for hook in entry["hooks"]:
                    self.assertEqual(hook["type"], "command")
                    self.assertIn("${PLUGIN_ROOT}/scripts/completion_guard.py", hook["command"])

    def test_manifest_and_skill_have_no_scaffold_placeholders(self):
        manifest = json.loads(MANIFEST.read_text())
        self.assertEqual(manifest["name"], "task-completion-guard")
        self.assertEqual(manifest["version"].split("+")[0], "0.1.0")
        combined = MANIFEST.read_text() + SKILL.read_text()
        self.assertNotIn("[TODO:", combined)


if __name__ == "__main__":
    unittest.main()
