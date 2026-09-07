"""Black-box regressions for handing a guarded task back to the user.

Only harness methods are reused from the independent-review suite, so unittest
discovery does not inherit or run that module's test cases a second time.
Lifecycle state is observed through hook subprocesses, never fabricated here.
"""

import json
import unittest

import test_independent_review as independent


WAITING_MESSAGE = "我停在第一阶段，等待你对造型的确认。"


class WaitingUserTests(unittest.TestCase):
    setUp = independent.IndependentReviewTests.setUp
    git = independent.IndependentReviewTests.git
    command = independent.IndependentReviewTests.command
    hook = independent.IndependentReviewTests.hook
    state_path = independent.IndependentReviewTests.state_path
    state = independent.IndependentReviewTests.state
    start = independent.IndependentReviewTests.start
    tool = independent.IndependentReviewTests.tool
    edit = independent.IndependentReviewTests.edit
    verify = independent.IndependentReviewTests.verify
    prepare = independent.IndependentReviewTests.prepare
    review_input = independent.IndependentReviewTests.review_input
    claim = independent.IndependentReviewTests.claim
    spawn = independent.IndependentReviewTests.spawn
    review_report = independent.IndependentReviewTests.review_report
    finish_review = independent.IndependentReviewTests.finish_review
    submit = independent.IndependentReviewTests.submit
    wrap_continuation = independent.IndependentReviewTests.wrap_continuation

    def stop_message(self, message=WAITING_MESSAGE):
        return self.hook("Stop", last_assistant_message=message)

    def assert_waiting(self, output):
        self.assertEqual(output, {})
        self.assertEqual(self.state()["phase"], "waiting_user")
        self.assertNotEqual(self.state()["phase"], "completed")

    def assert_still_guarded(self, output):
        self.assertEqual(output.get("decision"), "block", output)
        self.assertNotIn(self.state()["phase"], {"completed", "waiting_user"})

    def needs_user_audit(self):
        self.submit(
            status="needs_user",
            question="下一阶段采用圆形造型还是方形造型？",
            why_required="两种造型影响后续资源布局，需要用户选择后才能继续实现。",
            pending_criteria=["用户选择造型方案后完成第二阶段实现"],
        )

    def enter_structured_wait_after_block(self):
        self.start("请修改造型")
        blocked = self.stop_message("第一阶段还有一些工作没有完成。")
        self.assert_still_guarded(blocked)
        self.needs_user_audit()
        self.assert_waiting(self.stop_message("下一阶段采用圆形造型还是方形造型？"))
        return blocked["reason"]

    def test_screenshot_message_waits_without_any_audit(self):
        self.start("请修改造型")
        self.assertFalse(self.audit_path.exists())
        self.assert_waiting(self.stop_message())

    def test_specific_user_choice_can_pause_without_an_audit(self):
        for message in (
            "请确认采用 A 方案还是 B 方案，我会在你决定后继续实现。",
            "目前需要你决定头像用圆形还是方形，确认后我再继续。",
            "请先确认这个造型是否符合预期，我先停在这里。",
            "第一阶段预览已完成。\n\n我停在第一阶段，等待你对造型的确认。",
            "I'm pausing here until you confirm which design to use.",
            "Waiting for your decision on whether to use design A or design B.",
        ):
            with self.subTest(message=message):
                self.start("请修改造型")
                self.assert_waiting(self.stop_message(message))

    def test_waiting_message_takes_priority_over_a_stale_complete_audit(self):
        self.start("请修改造型")
        self.edit()
        self.verify()
        self.submit()
        self.edit(content="def calculate(value):\n    return value + 2\n")
        self.assert_waiting(self.stop_message())

    def test_waiting_message_takes_priority_over_invalid_complete_criteria(self):
        self.start("请修改造型")
        self.submit(criteria=[], remaining=["等待用户确认造型后继续第二阶段"])
        self.assert_waiting(self.stop_message())

    def test_waiting_message_takes_priority_over_failed_verification(self):
        self.start("请修改造型")
        self.edit()
        self.tool(
            "Bash", {"command": "python3 -m unittest discover -s tests"},
            {"exit_code": 1, "output": "FAILED (failures=1)"},
        )
        self.submit()
        self.assert_waiting(self.stop_message())

    def test_complete_audit_and_stdout_only_unknown_verification_can_wait(self):
        self.start("请修改造型")
        self.edit()
        self.tool(
            "Bash", {"command": "make check"},
            {"stdout": "Python syntax and plugin manifest checked.\n"},
        )
        self.submit()
        events_dir = self.state_path().parent / "events" / self.task_id
        events = [json.loads(path.read_text()) for path in events_dir.glob("*.json")]
        verification = [event for event in events if event["kind"] == "verification"]
        self.assertEqual(len(verification), 1)
        self.assertEqual(verification[0]["outcome"], "unknown")
        self.assert_waiting(self.stop_message())
        events = [json.loads(path.read_text()) for path in events_dir.glob("*.json")]
        verification = [event for event in events if event["kind"] == "verification"]
        self.assertEqual(verification[0]["outcome"], "unknown")

    def test_waiting_message_does_not_require_a_parseable_audit(self):
        self.start("请修改造型")
        self.audit_path.write_text('{"version": 1, broken JSON')
        self.assert_waiting(self.stop_message())

    def test_valid_needs_user_audit_remains_supported(self):
        self.start("请修改造型")
        self.needs_user_audit()
        self.assert_waiting(self.stop_message("下一阶段采用圆形造型还是方形造型？"))

    def test_waiting_user_repeated_stop_is_idempotent_even_if_audit_disappears(self):
        self.start("请修改造型")
        self.needs_user_audit()
        self.assert_waiting(self.stop_message("下一阶段采用圆形造型还是方形造型？"))
        before = self.state()
        self.audit_path.unlink()
        for message in ("", WAITING_MESSAGE, "上一条已向用户提出选择。"):
            with self.subTest(message=message):
                self.assert_waiting(self.stop_message(message))
                self.assertEqual(self.state()["task_id"], before["task_id"])
                self.assertEqual(self.state()["stop"], before["stop"])

    def test_old_bare_automatic_continuation_does_not_wake_waiting_user(self):
        reason = self.enter_structured_wait_after_block()
        before = self.state()
        self.hook("UserPromptSubmit", prompt=reason)
        self.assertEqual(self.state()["phase"], "waiting_user")
        self.assertEqual(self.state()["audit_not_before_ns"], before["audit_not_before_ns"])
        self.assertEqual(self.state()["stop"], before["stop"])
        self.assert_waiting(self.stop_message(""))

    def test_old_wrapped_automatic_continuation_does_not_wake_waiting_user(self):
        reason = self.enter_structured_wait_after_block()
        before = self.state()
        self.hook("UserPromptSubmit", prompt=self.wrap_continuation(reason))
        self.assertEqual(self.state()["phase"], "waiting_user")
        self.assertEqual(self.state()["audit_not_before_ns"], before["audit_not_before_ns"])
        self.assertEqual(self.state()["stop"], before["stop"])
        self.assert_waiting(self.stop_message(""))

    def test_legacy_task_tagged_automatic_continuation_does_not_wake_waiting_user(self):
        self.enter_structured_wait_after_block()
        before = self.state()
        prompt = "[task-completion-guard:continue %s] Finish all pending checks." % self.task_id
        self.hook("UserPromptSubmit", prompt=prompt)
        self.assertEqual(self.state()["phase"], "waiting_user")
        self.assertEqual(self.state()["audit_not_before_ns"], before["audit_not_before_ns"])
        self.assert_waiting(self.stop_message(""))

    def test_real_user_decision_resumes_the_same_task(self):
        self.start("请修改造型")
        self.needs_user_audit()
        self.assert_waiting(self.stop_message("下一阶段采用圆形造型还是方形造型？"))
        before = self.state()
        self.hook("UserPromptSubmit", prompt="确认，采用方形造型，继续实现。")
        state = self.state()
        self.assertEqual(state["phase"], "active")
        self.assertEqual(state["task_id"], before["task_id"])
        self.assertGreater(state["audit_not_before_ns"], before["audit_not_before_ns"])
        self.assert_still_guarded(self.stop_message("所有工作都完成了。"))

    def test_real_user_can_cancel_a_waiting_task(self):
        self.start("请修改造型")
        self.needs_user_audit()
        self.assert_waiting(self.stop_message("下一阶段采用圆形造型还是方形造型？"))
        self.hook("UserPromptSubmit", prompt="取消当前任务")
        self.assertEqual(self.state()["phase"], "cancelled")
        self.assertEqual(self.stop_message("已取消。"), {})

    def test_explicit_waiting_is_not_completed_even_with_a_passing_review(self):
        self.start("请修改造型")
        self.edit()
        self.verify()
        request, _ = self.prepare()
        self.finish_review(request)
        self.submit()
        self.assert_waiting(self.stop_message())

    def test_user_decision_invalidates_the_previous_independent_review(self):
        self.start("请修改造型")
        self.edit()
        self.verify()
        request, _ = self.prepare()
        self.finish_review(request)
        self.needs_user_audit()
        self.assert_waiting(self.stop_message("下一阶段采用圆形造型还是方形造型？"))
        self.hook("UserPromptSubmit", prompt="采用方形造型，并按方案 B 完成下一阶段。")
        self.assertEqual(self.state()["phase"], "active")
        self.submit()
        output = self.stop_message("确认项已处理，所有修改完成。")
        self.assert_still_guarded(output)

    def test_unpassed_review_does_not_prevent_a_user_decision_handoff(self):
        self.start("请修改造型")
        self.edit()
        self.verify()
        request, _ = self.prepare()
        self.finish_review(request, verdict="needs_changes", findings=["用户必须选择最终造型方案"])
        self.submit()
        self.assert_waiting(self.stop_message())

    def test_mentions_that_do_not_hand_off_a_concrete_user_decision_remain_guarded(self):
        messages = (
            "第一阶段还没完成，我稍后继续。",
            "我先停在第一阶段。",
            "造型仍需进一步完善，还有测试没跑。",
            "我确认代码还有问题，需要继续修复。",
            "确认后我会报告结果。",
            "完成测试并确认后我会报告结果。",
            "如果需要你确认造型，我会在完成后告诉你。",
            "如果你要先确认造型，我们可以到时再暂停。",
            "假设需要等待你对造型的确认，这里可以先暂停。",
            "造型已经得到你的确认，我会继续第二阶段。",
            "你已确认这个造型，无需再等待确认。",
            "当前不需要你确认造型，我会继续实现。",
            "无需等待你确认造型，后续工作可直接继续。",
            "这一步不是等待你确认造型，仍然有实现工作要做。",
            "我没有停下来等待你对造型的确认。",
            "界面上新增了一个“等待你确认造型”的提示文案。",
            "日志中出现了：等待你对造型的确认。",
            "用户原话是：“我停在第一阶段，等待你对造型的确认。”",
            "示例文案：我停在第一阶段，等待你对造型的确认。",
            "`我停在第一阶段，等待你对造型的确认。`",
            "> 我停在第一阶段，等待你对造型的确认。",
            "```text\n我停在第一阶段，等待你对造型的确认。\n```",
            "~~~text\n我停在第一阶段，等待你对造型的确认。\n~~~",
            "I'm still implementing the design and have not finished the tests.",
            "If you need to confirm the design, I will ask when the work is ready.",
            "You already confirmed the design; implementation continues.",
            "I am not waiting for your confirmation of the design.",
            "No confirmation is needed; I will continue implementing the design.",
        )
        for message in messages:
            with self.subTest(message=message):
                self.start("请修改造型")
                self.assert_still_guarded(self.stop_message(message))

    def test_single_quoted_and_system_status_mentions_are_not_user_handoffs(self):
        for message in (
            "'我停在第一阶段，等待你对造型的确认。'",
            "日志原文是：'我停在第一阶段，等待你对造型的确认。'",
            "系统当前状态：等待用户确认，我会继续补齐实现。",
            "系统当前状态：等待用户确认。\n我会继续实现剩余功能。",
        ):
            with self.subTest(message=message):
                self.start("请修改造型")
                self.assert_still_guarded(self.stop_message(message))

    def test_work_before_confirmation_and_conditional_approval_remain_guarded(self):
        for message in (
            "等待确认之前我会先补齐剩余测试。",
            "等待你确认之前，我会先补齐剩余测试。",
            "Waiting approval is only necessary if deployment requested.",
            "Waiting for your approval is only necessary if deployment is requested.",
        ):
            with self.subTest(message=message):
                self.start("请修改造型")
                self.assert_still_guarded(self.stop_message(message))

    def test_pause_and_concrete_confirmation_in_separate_sentences_can_wait(self):
        self.start("请修改造型")
        self.assert_waiting(self.stop_message("我暂停当前修改。请你确认设计是否通过。"))

    def test_explanation_only_followup_preserves_waiting_user(self):
        self.start("请修改造型")
        self.needs_user_audit()
        self.assert_waiting(self.stop_message("下一阶段采用圆形造型还是方形造型？"))
        before = self.state()
        self.hook("UserPromptSubmit", prompt="只解释一下方案，不要继续修改")
        self.assertEqual(self.state()["phase"], "waiting_user")
        self.assertEqual(self.state()["task_id"], before["task_id"])
        self.assert_waiting(self.stop_message("方案 A 使用圆形轮廓，方案 B 使用方形轮廓，两者布局不同。"))

    def test_nonstopping_system_flow_and_single_quote_variants_remain_guarded(self):
        for message in (
            "我不会暂停。请你确认方案的同时，我会继续完成剩余修改。",
            "等待你确认不是当前任务的停止条件，我会继续完成实现。",
            "系统当前停在确认页面，等待用户确认后提交表单。",
            "'I'm pausing here until you confirm.'",
            "‘我停在第一阶段，等待你对造型的确认。’",
            "‘I'm pausing here until you confirm.’",
        ):
            with self.subTest(message=message):
                self.start("请修改造型")
                self.assert_still_guarded(self.stop_message(message))

    def test_user_question_preserves_waiting_until_an_explicit_decision(self):
        self.start("请修改造型")
        self.needs_user_audit()
        self.assert_waiting(self.stop_message("下一阶段采用圆形造型还是方形造型？"))
        before = self.state()
        self.hook("UserPromptSubmit", prompt="为什么选择这个造型？")
        self.assertEqual(self.state()["phase"], "waiting_user")
        self.assertEqual(self.state()["task_id"], before["task_id"])
        self.assert_waiting(self.stop_message("这个造型能突出人物轮廓，也能让小尺寸图标更容易辨认。"))
        self.hook("UserPromptSubmit", prompt="确认通过，继续下一阶段")
        self.assertEqual(self.state()["phase"], "active")
        self.assertEqual(self.state()["task_id"], before["task_id"])
        self.assertGreater(self.state()["audit_not_before_ns"], before["audit_not_before_ns"])


if __name__ == "__main__":
    unittest.main()
