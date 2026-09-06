"""Regression cases for the actual hook's structured/textual tool_response."""

import importlib.util
import json
from pathlib import Path
import unittest


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "verification_output.py"
SPEC = importlib.util.spec_from_file_location("verification_output", MODULE)
VERIFICATION_OUTPUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFICATION_OUTPUT)
response_outcome = VERIFICATION_OUTPUT.response_outcome

NODE_TAP = """TAP version 13
# Subtest: unique characters across 500 eight-player games
ok 1 - unique characters across 500 eight-player games
  ---
  duration_ms: 143.583
  ...
1..18
# tests 18
# suites 0
# pass 18
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 263.751
"""
UNITTEST_OK = """..................
----------------------------------------------------------------------
Ran 18 tests in 0.074s

OK
"""


class VerificationOutputTests(unittest.TestCase):
    def test_structured_shell_completion(self):
        self.assertEqual(response_outcome({"exit_code": 0, "output": ""}), "success")
        self.assertEqual(response_outcome({"exit_code": 1, "output": ""}), "failed")
        self.assertEqual(response_outcome({"return_code": "-9"}), "failed")

    def test_codex_text_shell_completion(self):
        output = "Chunk ID: c4e909\nWall time: 0.112 seconds\nProcess exited with code 0\nFinal output:\n"
        self.assertEqual(response_outcome(output), "success")
        self.assertEqual(response_outcome(output.replace("code 0", "code 1")), "failed")
        self.assertEqual(response_outcome(output.replace("code 0", "code -9")), "failed")

    def test_text_exit_code_variants(self):
        for output in ("exit_code: 0", "Exit code: 0", "returncode = 0", "return_code: 0"):
            with self.subTest(output=output):
                self.assertEqual(response_outcome(output), "success")

    def test_json_string_inside_mcp_content(self):
        response = {"content": [{"type": "text", "text": json.dumps({"exit_code": 0, "output": NODE_TAP})}]}
        self.assertEqual(response_outcome(json.dumps(response)), "success")
        self.assertEqual(response_outcome(json.dumps(json.dumps(response))), "success")

    def test_separately_encoded_sibling_results_do_not_hide_failure(self):
        # Decoded temporary objects may reuse a Python id. Cycle protection
        # must track only ancestors, not discard later independent results.
        outputs = [json.dumps({"exit_code": 0}) for _ in range(20)]
        outputs.append(json.dumps({"exit_code": 1}))
        self.assertEqual(response_outcome(outputs), "failed")

    def test_mcp_text_shell_completion_without_json(self):
        self.assertEqual(response_outcome({"content": [{"type": "text", "text": "Process exited with code 0\nFinal output:\n"}]}), "success")

    def test_node_tap_complete_summary(self):
        self.assertEqual(response_outcome({"stdout": NODE_TAP}), "success")

    def test_node_spec_complete_summary_with_ansi(self):
        spec = NODE_TAP.replace("# ", "ℹ ")
        spec = "\x1b[34m" + spec.replace("\n", "\x1b[0m\n\x1b[34m") + "\x1b[0m"
        self.assertEqual(response_outcome(spec), "success")

    def test_partial_node_summary_not_complete(self):
        self.assertEqual(response_outcome(NODE_TAP[:NODE_TAP.index("# duration_ms")]), "unknown")
        self.assertEqual(response_outcome("TAP version 13\nok 1 - first test\n"), "unknown")

    def test_node_empty_skipped_only_and_inconsistent_summaries_not_success(self):
        for output in (
            NODE_TAP.replace("tests 18", "tests 0").replace("pass 18", "pass 0"),
            NODE_TAP.replace("pass 18", "pass 0").replace("skipped 0", "skipped 18"),
            NODE_TAP.replace("pass 18", "pass 17"),
        ):
            self.assertEqual(response_outcome(output), "unknown")

    def test_node_failure_and_cancellation_override_exit_zero(self):
        for label in ("fail", "cancelled"):
            output = NODE_TAP.replace("pass 18", "pass 17").replace(label + " 0", label + " 1")
            self.assertEqual(response_outcome({"exit_code": 0, "output": output}), "failed")

    def test_unittest_complete_summary(self):
        self.assertEqual(response_outcome({"stderr": UNITTEST_OK}), "success")
        self.assertEqual(response_outcome(UNITTEST_OK.replace("18 tests", "1 test")), "success")
        self.assertEqual(response_outcome(UNITTEST_OK.replace("OK", "OK (skipped=1, expected failures=1)")), "success")

    def test_unittest_incomplete_or_narrative_is_unknown(self):
        for output in ("OK", "Ran 18 tests in 0.074s\n", "All 18 tests passed.", "tests passed", "Done! Tests passed."):
            with self.subTest(output=output):
                self.assertEqual(response_outcome(output), "unknown")

    def test_unittest_failure_overrides_success_status(self):
        output = UNITTEST_OK.replace("OK", "FAILED (failures=1, errors=1)")
        self.assertEqual(response_outcome({"status": "completed", "stderr": output}), "failed")

    def test_nested_failures_override_success_regardless_of_order(self):
        pairs = (
            [{"exit_code": 0}, {"exit_code": 1}],
            [{"exit_code": 0}, {"isError": True}],
            [{"status": "passed"}, {"text": "Process exited with code 2"}],
            [{"stdout": NODE_TAP}, {"status": "timed_out"}],
            [{"output": "Process exited with code 0\nProcess exited with code -15"}],
        )
        for pair in pairs:
            self.assertEqual(response_outcome(pair), "failed")
            self.assertEqual(response_outcome(list(reversed(pair))), "failed")

    def test_running_process_even_with_passing_summary_is_unknown(self):
        for response in (
            {"session_id": 4321, "output": NODE_TAP},
            {"status": "running", "output": UNITTEST_OK},
            "Process running with session ID 4321\n" + NODE_TAP,
            "Script running with cell ID 17\n" + UNITTEST_OK,
        ):
            self.assertEqual(response_outcome(response), "unknown")

    def test_failed_running_process_is_failed(self):
        self.assertEqual(response_outcome({"status": "running", "isError": True}), "failed")

    def test_nonterminal_empty_null_and_boolean_exit_codes_are_unknown(self):
        for response in ({}, None, [], {"exit_code": None}, {"exit_code": False}, {"isError": False}, {"output": ""}):
            self.assertEqual(response_outcome(response), "unknown")

    def test_status_inside_prose_not_an_exit_record(self):
        for response in ("Expected exit_code: 0 in the next result.", "The process exited with code 0 in yesterday's run.", "{not valid JSON}"):
            self.assertEqual(response_outcome(response), "unknown")

    def test_precise_patch_done_response_retains_compatibility(self):
        self.assertEqual(response_outcome({"isError": False, "output": "Done!"}), "success")

    def test_recursion_is_bounded(self):
        recursive = []
        recursive.append(recursive)
        self.assertEqual(response_outcome(recursive), "unknown")
        deep = {"exit_code": 0}
        for _ in range(24):
            deep = {"result": deep}
        self.assertEqual(response_outcome(deep), "unknown")


if __name__ == "__main__":
    unittest.main()
