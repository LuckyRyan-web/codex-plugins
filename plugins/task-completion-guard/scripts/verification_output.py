"""Read terminal outcomes from structured and textual Codex tool responses.

This module does not retain tool output. A terminal failure takes precedence
over successes anywhere in a response; unfinished commands are not successful
verification merely because their output includes a passing test summary.
"""

import json
import re


EXIT_KEYS = {"exit_code", "exitcode", "returncode", "return_code"}
SUCCESS_STATUSES = {"ok", "success", "succeeded", "completed", "passed"}
FAILURE_STATUSES = {"failed", "failure", "error", "timed_out", "timeout", "denied"}
RUNNING_STATUSES = {"running", "in_progress", "pending", "queued"}
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
EXIT_LINE_RE = re.compile(
    r"^\s*(?:process exited with code\s+|"
    r"(?:exit[_ ]?code|return[_ ]?code)\s*[:=]\s*)(-?\d+)\s*\.?\s*$",
    re.I | re.M,
)
RUNNING_LINE_RE = re.compile(
    r"^\s*(?:process running with session (?:id|identifier)|"
    r"script running with cell id)\b[^\n]*$", re.I | re.M
)
FAILURE_LINE_RE = re.compile(
    r"^\s*(?:error:\s*)?(?:permission denied|timed out|tool call failed)"
    r"(?:\s*[:.].*)?\s*$", re.I | re.M
)
NODE_LINE_RE = re.compile(
    r"^\s*(?:#|ℹ)\s+(tests|pass|fail|cancelled|skipped|todo|duration_ms)"
    r"\s+(\d+(?:\.\d+)?)\s*$", re.M
)
UNITTEST_SUMMARY_RE = re.compile(
    r"^Ran ([1-9]\d*) tests? in \d+(?:\.\d+)?s\s*\n\s*"
    r"(OK(?: \((?:skipped=\d+|expected failures=\d+)"
    r"(?:, (?:skipped=\d+|expected failures=\d+))*\))?|"
    r"FAILED(?: \([^\n]*\))?)\s*$", re.M
)


def _exit_code(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


def response_outcome(value):
    """Return ``success``, ``failed`` or ``unknown`` for one tool response.

    Text success requires an actual exit-status line or a completed runner
    summary. Plain prose such as "tests passed" is deliberately insufficient.
    JSON strings in MCP content blocks are decoded before examining outcomes.
    """
    outcomes = set()
    seen = set()

    def inspect_text(raw, depth):
        stripped = raw.strip()
        # Hook tool_response can be a JSON string, with another encoded JSON
        # response in an MCP text block. Decode only complete JSON values.
        if stripped[:1] in {'{', '[', '"'}:
            try:
                decoded = json.loads(stripped)
            except (ValueError, RecursionError):
                pass
            else:
                visit(decoded, depth + 1)
                return

        output = ANSI_RE.sub("", raw).replace("\r\n", "\n")
        for match in EXIT_LINE_RE.finditer(output):
            outcomes.add("success" if int(match.group(1)) == 0 else "failed")
        if RUNNING_LINE_RE.search(output):
            outcomes.add("running")
        if FAILURE_LINE_RE.search(output):
            outcomes.add("failed")
        if stripped == "Done!":
            # Preserve the existing apply_patch response contract.
            outcomes.add("success")

        # Node --test TAP and spec reporters share these final counters.
        # duration_ms closes the summary; a partial stream is insufficient.
        counters = {}
        for match in NODE_LINE_RE.finditer(output):
            name, number = match.groups()
            if name == "tests":
                counters = {}
            counters[name] = float(number) if name == "duration_ms" else _exit_code(number)
            if name in {"fail", "cancelled"} and counters[name]:
                outcomes.add("failed")
            if name == "duration_ms":
                required = {"tests", "pass", "fail", "cancelled", "skipped", "todo"}
                if required.issubset(counters) and all(counters[key] is not None for key in required):
                    counts = sum(counters[key] for key in required - {"tests"})
                    if (counters["tests"] > 0 and counts == counters["tests"]
                            and counters["pass"] > 0 and counters["fail"] == 0
                            and counters["cancelled"] == 0):
                        outcomes.add("success")
                counters = {}

        for match in UNITTEST_SUMMARY_RE.finditer(output):
            outcomes.add("success" if match.group(2).startswith("OK") else "failed")

    def visit(node, depth=0):
        # Bound nested serialization and tolerate cyclic Python values even
        # though normal hook input is JSON and cannot contain a cycle.
        if depth > 20:
            outcomes.add("incomplete")
            return
        if isinstance(node, (dict, list)):
            if id(node) in seen:
                return
            seen.add(id(node))
        if isinstance(node, dict):
            normalized = {str(key).lower(): item for key, item in node.items()}
            terminal = False
            for key, item in normalized.items():
                if key in {"iserror", "is_error"} and item is True:
                    outcomes.add("failed")
                elif key in EXIT_KEYS:
                    code = _exit_code(item)
                    if code is not None:
                        terminal = True
                        outcomes.add("success" if code == 0 else "failed")
                elif key in {"status", "outcome", "result"} and isinstance(item, str):
                    status = item.strip().lower()
                    if status in FAILURE_STATUSES:
                        terminal = True
                        outcomes.add("failed")
                    elif status in SUCCESS_STATUSES:
                        terminal = True
                        outcomes.add("success")
                    elif status in RUNNING_STATUSES:
                        outcomes.add("running")
            # exec_command returns session_id only while the process lives.
            # Ignore session identifiers in unrelated metadata objects.
            if (not terminal and normalized.get("session_id") is not None
                    and any(key in normalized for key in ("output", "stdout", "stderr"))):
                outcomes.add("running")
            for item in node.values():
                visit(item, depth + 1)
            seen.remove(id(node))
        elif isinstance(node, list):
            for item in node:
                visit(item, depth + 1)
            seen.remove(id(node))
        elif isinstance(node, str):
            inspect_text(node, depth)

    visit(value)
    if "failed" in outcomes:
        return "failed"
    if "running" in outcomes or "incomplete" in outcomes:
        return "unknown"
    return "success" if "success" in outcomes else "unknown"
