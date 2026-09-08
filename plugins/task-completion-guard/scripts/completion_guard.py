#!/usr/bin/env python3
"""Lifecycle hook for the Task Completion Guard Codex plugin.

The hook deliberately separates semantic judgment from mechanical checks:
Codex saves a structured completion audit in a private local file,
while this script validates that declaration against observable tool events.
It does not parse the unstable Codex transcript format. Tool events retain
hashes; private independent-review handoffs retain the user requirements.
"""

from __future__ import print_function

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import shlex
import xml.etree.ElementTree as ET

from verification_output import response_outcome
import review_gate
from user_wait import waiting_for_user, clarification_only
from review_snapshot import SnapshotError
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


SCHEMA_VERSION = 1
MAX_BLOCKS = 3
MAX_MARKER_BYTES = 64 * 1024
CONTINUATION_PREFIX = "[task-completion-guard:continue"
MARKER_RE = re.compile(
    r"<!--\s*task-completion-guard:(\{.*\})\s*-->", re.DOTALL
)

TERMINAL_PHASES = {"completed", "cancelled", "blocked_external"}

NO_CHANGE_PATTERNS = [
    re.compile(r"(?:只|仅).{0,10}(?:解释|分析|审查|评审|检查|建议|方案|规划|计划)"),
    re.compile(r"(?:不要|别|无需|不用).{0,10}(?:修改|改动|写代码|执行|实现|落地)"),
    re.compile(r"(?:先|暂时).{0,5}(?:不要|别).{0,10}(?:修改|改动|写|实现|执行)"),
    re.compile(
        r"^\s*(?:请|麻烦)?\s*(?:帮我|给我)?\s*(?:写|做|出|给)"
        r"(?:一个|一份)?[^。.!！?？]{0,30}(?:方案|计划|规划|设计)"
        r"(?:[。.!！?？]|$)"
    ),
    re.compile(r"\b(?:read[- ]only|explain only|analysis only|review only|plan only)\b", re.I),
    re.compile(
        r"^\s*(?:please\s+)?(?:draft|write|create|give me)\s+(?:an?\s+)?"
        r"[^.!?]{0,30}\b(?:plan|proposal|design)\b[.!?]?\s*$",
        re.I,
    ),
    re.compile(
        r"\b(?:do not|don't|dont|without)\b.{0,30}"
        r"\b(?:change|edit|modify|implement|write|execute)\b",
        re.I,
    ),
]

ACTION_WORDS_ZH = (
    "实现|开发|新增|添加|增加|修复|修一下|修改|改一下|改成|重构|迁移|"
    "升级|降级|删除|移除|接入|集成|部署|发布|配置|安装|创建|生成|编写|"
    "写一个|写个|写下|写|做一个|做个|做下|完善|补充|优化|替换|落地|"
    "处理一下|处理|解决|搞定|修掉|改掉"
)
ACTION_WORDS_EN = (
    "implement|build|add|create|write|fix|change|update|refactor|migrate|"
    "upgrade|remove|delete|integrate|deploy|configure|install|optimize|replace"
)
ACTION_PATTERNS = [
    re.compile(
        r"^\s*(?:请|麻烦|劳驾)?\s*(?:直接|现在|继续|开始)?\s*"
        r"(?:帮我|给我|把|将)?\s*(?:" + ACTION_WORDS_ZH + r")"
    ),
    re.compile(r"(?:请|麻烦|能不能|可以)?\s*帮我.{0,40}(?:" + ACTION_WORDS_ZH + r")"),
    re.compile(r"(?:你)?\s*(?:" + ACTION_WORDS_ZH + r").{0,6}(?:吧|一下)[。.!！]?$"),
    re.compile(
        r"(?:看一下|查一下|排查).{0,50}"
        r"(?:修复|修掉|改掉|处理(?:一下)?|解决|搞定)"
    ),
    re.compile(
        r"^\s*(?:please\s+)?(?:can you\s+|could you\s+|help me\s+)?"
        r"(?:" + ACTION_WORDS_EN + r")\b",
        re.I,
    ),
    re.compile(r"\bplease\b.{0,40}\b(?:" + ACTION_WORDS_EN + r")\b", re.I),
    re.compile(
        r"\b(?:look into|investigate|check)\b.{0,60}"
        r"\b(?:fix|resolve|repair|correct)\b",
        re.I,
    ),
]

OPT_OUT_RE = re.compile(
    r"\[completion-guard:off\]|(?:本次|这次).{0,6}(?:不要|别|关闭|跳过).{0,8}"
    r"(?:完成门禁|completion guard)|\b(?:skip|disable)\s+(?:the\s+)?completion guard\b",
    re.I,
)
CANCEL_RE = re.compile(
    r"^\s*(?:请)?\s*(?:停止|暂停|取消|先停(?:一下)?|先到这里|不用继续|不要继续|"
    r"别继续|先这样吧)(?:这个|当前)?(?:任务)?[。.!！]?\s*$",
    re.I,
)
RESUME_RE = re.compile(
    r"^\s*(?:继续(?:完成|刚才)?|接着做|恢复刚才|go on\b|continue\b|resume\b)",
    re.I,
)

COMPLEX_RE = re.compile(
    r"功能|feature|重构|refactor|迁移|migrat|部署|deploy|权限|permission|"
    r"认证|auth|接口|\bapi\b|数据库|database|端到端|e2e|页面|workflow|系统",
    re.I,
)

VERIFICATION_PATTERNS = [
    ("pnpm test", re.compile(r"\bpnpm\b[^\n;|&]*\b(?:run\s+)?test\b", re.I)),
    ("npm test", re.compile(r"\bnpm\b[^\n;|&]*\b(?:run\s+)?test\b", re.I)),
    ("yarn test", re.compile(r"\byarn\b[^\n;|&]*\btest\b", re.I)),
    ("node test", re.compile(r"\bnode\b[^\n;|&]*\s--test\b", re.I)),
    ("pytest", re.compile(r"\bpytest\b|\bpython(?:3)?\s+-m\s+pytest\b", re.I)),
    ("unittest", re.compile(r"\bpython(?:3)?\s+-m\s+unittest\b", re.I)),
    ("cargo test", re.compile(r"\bcargo\s+(?:test|check)\b", re.I)),
    ("go test", re.compile(r"\bgo\s+test\b", re.I)),
    ("lint", re.compile(r"\b(?:pnpm|npm|yarn|bun)\b[^\n;|&]*\blint\b", re.I)),
    ("typecheck", re.compile(r"\btype[-:]?check\b|\btsc\b[^\n;|&]*--noEmit\b", re.I)),
    ("build", re.compile(r"\b(?:pnpm|npm|yarn|bun)\b[^\n;|&]*\bbuild\b", re.I)),
    ("git diff --check", re.compile(r"\bgit\s+diff\s+--check\b", re.I)),
    ("make check", re.compile(r"\bmake\s+(?:test|check|verify|lint)\b", re.I)),
    ("gradle test", re.compile(r"\b(?:gradle|gradlew)\b[^\n;|&]*\btest\b", re.I)),
    ("maven test", re.compile(r"\bmvn\b[^\n;|&]*\b(?:test|verify)\b", re.I)),
]

MUTATION_RE = re.compile(
    r"(?:^|[;&|]\s*|\s)(?:"
    r"git\s+(?:add|commit|switch|checkout|merge|rebase|cherry-pick|apply)|"
    r"(?:cp|mv|rm|mkdir|touch|chmod|chown|install)\s|"
    r"sed\s+-[^\n;|&]*i\b|perl\s+-[^\n;|&]*i\b|"
    r"(?:pnpm|npm|yarn|bun)\s+(?:add|remove|install|update|upgrade)\b|"
    r"docker\s+(?:compose\s+)?(?:up|down|build|pull|push)\b|"
    r"kubectl\s+(?:apply|delete|create|patch|set)\b|"
    r"terraform\s+(?:apply|destroy|import)\b"
    r")",
    re.I,
)

# Commands outside this deliberately narrow allowlist are treated as possible
# mutations once a task is active. This prevents a custom rewrite script from
# silently making an earlier verification stale. Shell control, redirection,
# command substitution, and environment expansion intentionally fail the
# allowlist and therefore take the conservative path.
READ_ONLY_BASH_RE = re.compile(
    r"^\s*(?:"
    r"(?:pwd|ls|rg|grep|cat|head|tail|wc|stat|file|which|printenv|jq)\b|"
    r"command\s+-v\b|"
    r"git\s+(?:status|diff|show|log|rev-parse|ls-files)\b"
    r")[^;&|<>`$]*\s*$",
    re.I,
)


class GuardError(Exception):
    pass


class CorruptState(GuardError):
    pass


class LockTimeout(GuardError):
    pass


def _json_bytes(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8", "replace")


def digest(value):
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, str):
        data = value.encode("utf-8", "replace")
    else:
        data = _json_bytes(value)
    return hashlib.sha256(data).hexdigest()


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")


def fail_open(reason_code):
    emit(
        {
            "systemMessage": (
                "完成检查暂时不可用，本次未完成自动验收。"
            )
        }
    )


def plugin_data_root():
    raw = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if not raw or "${" in raw:
        raise GuardError("plugin_data_unavailable")
    root = Path(raw).expanduser().resolve() / "completion-guard" / "v1"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def session_directory(root, payload):
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if not isinstance(session_id, str) or not session_id:
        raise GuardError("missing_session_id")
    if not isinstance(cwd, str) or not cwd:
        raise GuardError("missing_cwd")
    cwd_key = os.path.realpath(cwd)
    key = digest(session_id + "\0" + cwd_key)
    path = root / "sessions" / key
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path, digest(session_id), digest(cwd_key)


@contextlib.contextmanager
def locked(session_dir):
    lock_path = session_dir / ".state.lock"
    handle = open(str(lock_path), "a+")
    try:
        try:
            os.chmod(str(lock_path), 0o600)
        except OSError:
            pass
        if fcntl is not None:
            acquired = False
            for _ in range(40):
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    time.sleep(0.025)
            if not acquired:
                raise LockTimeout("state_lock_timeout")
        yield
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def atomic_write_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(".%s.%s.%s.tmp" % (path.name, os.getpid(), secrets.token_hex(4)))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def read_state(path):
    if not path.exists():
        return None
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CorruptState("state_unreadable") from exc
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise CorruptState("state_schema_invalid")
    if not isinstance(state.get("task_id"), str):
        raise CorruptState("state_task_id_invalid")
    return state


def write_state(path, state):
    state["updated_at_ns"] = time.time_ns()
    atomic_write_json(path, state)


def ensure_file_protocol(state, session_dir):
    if state.get("declaration_protocol") == "file":
        return False
    folder = Path(tempfile.gettempdir()) / "codex-task-completion-guard" / session_dir.name
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    state["declaration_protocol"] = "file"
    state["audit_path"] = str(folder / (state["task_id"] + ".json"))
    state["audit_not_before_ns"] = time.time_ns()
    return True


def new_state(payload, session_key, cwd_key, source, minimum):
    now = time.time_ns()
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": "tcg_%s" % secrets.token_hex(6),
        "session_key": session_key,
        "cwd_key": cwd_key,
        "origin_turn_key": digest(str(payload.get("turn_id") or "")),
        "mode": "enforce",
        "phase": "active",
        "activation_source": source,
        "minimum_criteria": int(minimum),
        "prompt_hash": digest(str(payload.get("prompt") or "")),
        "created_at_ns": now,
        "updated_at_ns": now,
        "review": {"version": 1, "attempts": [], "starts": {}, "current": None},
        "stop": {
            "block_count": 0,
            "stalled_count": 0,
            "last_signature": None,
            "last_reason_hash": None,
        },
    }


def normalize_prompt(prompt):
    return " ".join(prompt.strip().split())


def classify_prompt(prompt, permission_mode):
    text = normalize_prompt(prompt)
    if not text:
        return "observe"
    if OPT_OUT_RE.search(text):
        return "cancel"
    if len(text) <= 80 and CANCEL_RE.match(text):
        return "cancel"
    if permission_mode == "plan":
        return "exempt"
    if any(pattern.search(text) for pattern in NO_CHANGE_PATTERNS):
        return "exempt"
    if any(pattern.search(text) for pattern in ACTION_PATTERNS):
        return "enforce"
    return "observe"


def minimum_criteria(prompt):
    text = normalize_prompt(prompt)
    minimum = 1
    if len(text) >= 60:
        minimum = 2
    connectors = len(re.findall(r"以及|同时|并且|而且|、|\band\b|\balso\b", text, re.I))
    if connectors >= 1:
        minimum = max(minimum, 2)
    if connectors >= 2 or COMPLEX_RE.search(text):
        minimum = max(minimum, 3)
    if len(text) >= 180:
        minimum = max(minimum, 4)
    return min(minimum, 4)


def audit_example(state):
    return {
        "version": 1,
        "task_id": state["task_id"],
        "status": "complete",
        "criteria": [{"id": "C%d" % (i + 1), "description": "meaningful acceptance item",
                      "status": "done", "evidence": "specific verification evidence"}
                     for i in range(state["minimum_criteria"])],
        "remaining": [], "summary": "concise completion summary",
        "verification": {"status": "passed", "summary": "command or check that passed"},
    }


def guard_context(state, activation_note=None):
    command = "python3 %s submit --audit-file %s" % (
        shlex.quote(str(Path(__file__).resolve())), shlex.quote(state["audit_path"]))
    note = " Activation: %s." % activation_note if activation_note else ""
    return (
        "Task Completion Guard is ACTIVE for task %s.%s\n"
        "This local-file declaration protocol supersedes earlier inline/HTML marker instructions. "
        "Never append an audit marker or audit JSON to the user-facing answer.\n"
        "If a required user confirmation, choice, authorization or stage approval is pending, "
        "pause and submit status=needs_user with a concrete question, why_required and pending_criteria. "
        "Do not submit complete for a stage that is waiting for the user's decision. "
        "Completion-only checks and independent review are not prerequisites for this waiting state. "
        "Finish the entire authorized task, derive at least %d meaningful acceptance criteria, "
        "and verify the final changes. Save the audit after all business changes and verification. "
        "Send this JSON schema to the following command's stdin (replace placeholders with evidence):\n%s\n%s\n"
        "For a required user decision use this alternative JSON instead:\n%s\n"
        "The command writes only a private temporary audit file. Do not write to plugin state or event logs. "
        "Use status=needs_user with question, why_required and pending_criteria only for a required user decision; "
        "use status=blocked with reason only for an observed external blocker. "
        "Do not fabricate evidence or bypass failed checks. "
        "Keep the final answer natural and do not narrate audit retries."
        % (state["task_id"], note, state["minimum_criteria"], command,
           json.dumps(audit_example(state), ensure_ascii=False),
           json.dumps({"version": 1, "task_id": state["task_id"], "status": "needs_user",
                       "question": "the specific choice or stage approval needed from the user",
                       "why_required": "why the next authorized step depends on that decision",
                       "pending_criteria": ["the criterion that must wait for the user"]}, ensure_ascii=False))
    ) + review_gate.context(state)


def additional_context(event_name, text):
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }


def bash_command(payload):
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
    return ""


def classify_tool(payload):
    tool_name = str(payload.get("tool_name") or "")
    lowered = tool_name.lower()
    if lowered in {"apply_patch", "edit", "write"}:
        return "mutation", "file edit"
    if lowered != "bash":
        return "activity", "tool activity"

    command = bash_command(payload)
    if "completion_guard.py" in command or "task-completion-guard" in command:
        return "guard", "guard command"
    for label, pattern in VERIFICATION_PATTERNS:
        if pattern.search(command):
            return "verification", label
    if MUTATION_RE.search(command):
        return "mutation", "shell mutation"
    return "activity", "shell activity"


def refine_guarded_bash_kind(payload, kind, label):
    """Conservatively classify an otherwise unknown Bash command.

    This refinement is only used after a task is active or suspended, so
    ordinary read-only investigation cannot enroll a task by itself. An
    unknown command may be a project-specific generator or rewrite script;
    treating it as a possible mutation makes any earlier verification stale.
    """
    if str(payload.get("tool_name") or "").lower() != "bash" or kind != "activity":
        return kind, label
    command = bash_command(payload)
    if READ_ONLY_BASH_RE.fullmatch(command):
        return kind, label
    return "possible_mutation", "unclassified shell command"


def event_directory(session_dir, task_id):
    return session_dir / "events" / task_id


def record_event(session_dir, state, payload, kind, label):
    tool_use_id = payload.get("tool_use_id")
    if isinstance(tool_use_id, str) and tool_use_id:
        event_key = digest(tool_use_id)
    else:
        event_key = digest(
            {
                "turn": payload.get("turn_id"),
                "tool": payload.get("tool_name"),
                "input": payload.get("tool_input"),
                "pid": os.getpid(),
                "time": time.time_ns(),
            }
        )
    directory = event_directory(session_dir, state["task_id"])
    path = directory / (event_key + ".json")
    if path.exists():
        return
    event = {
        "schema_version": SCHEMA_VERSION,
        "task_id": state["task_id"],
        "event_id": event_key,
        "at_ns": time.time_ns(),
        "tool_name": str(payload.get("tool_name") or "unknown")[:80],
        "kind": kind,
        "label": label,
        "outcome": response_outcome(payload.get("tool_response")),
        "input_hash": digest(payload.get("tool_input")),
        "response_hash": digest(payload.get("tool_response")),
    }
    atomic_write_json(path, event)


def read_events(session_dir, task_id):
    directory = event_directory(session_dir, task_id)
    if not directory.exists():
        return []
    events = []
    for path in list(directory.glob("*.json"))[:2000]:
        try:
            with open(str(path), "r", encoding="utf-8") as handle:
                event = json.load(handle)
            if isinstance(event, dict) and event.get("task_id") == task_id:
                events.append(event)
        except (OSError, ValueError):
            continue
    events.sort(key=lambda item: int(item.get("at_ns") or 0))
    return events


def parse_marker(message):
    if not isinstance(message, str) or not message:
        return None, "completion marker is missing", "missing"
    tail = message[-MAX_MARKER_BYTES:]
    matches = list(MARKER_RE.finditer(tail))
    if not matches:
        return None, "completion marker is missing", "missing"
    raw = matches[-1].group(1).strip()
    try:
        marker = json.loads(raw)
    except ValueError:
        return None, "completion marker is not valid JSON", digest(raw)
    if not isinstance(marker, dict):
        return None, "completion marker must be a JSON object", digest(raw)
    return marker, None, digest(raw)


def read_audit(state, facts, message):
    if state.get("declaration_protocol") != "file":
        return parse_marker(message)  # Compatibility for a hook already awaiting Stop during upgrade.
    path = Path(state["audit_path"])
    try:
        with path.open("rb") as handle:
            metadata = os.fstat(handle.fileno())
            raw = handle.read(MAX_MARKER_BYTES + 1)
    except OSError:
        return None, "local completion audit is missing", "missing"
    if len(raw) > MAX_MARKER_BYTES:
        return None, "local completion audit exceeds size limit", digest(raw)
    latest_mutation = max([int(e.get("at_ns") or 0) for e in facts["mutations"]] or [0])
    if metadata.st_mtime_ns < max(int(state.get("audit_not_before_ns") or 0), latest_mutation):
        return None, "local completion audit is stale; submit after the latest input and changes", digest(raw)
    try:
        audit = json.loads(raw)
    except (ValueError, UnicodeError):
        return None, "local completion audit is not valid JSON", digest(raw)
    if not isinstance(audit, dict):
        return None, "local completion audit must be a JSON object", digest(raw)
    return audit, None, digest(raw)


def nonempty_text(value, minimum=1):
    return isinstance(value, str) and len(value.strip()) >= minimum


def evidence_summary(events):
    mutations = [
        event
        for event in events
        if event.get("kind") in {"mutation", "possible_mutation"}
    ]
    observed_mutations = [event for event in mutations if event.get("outcome") != "failed"]
    verifications = [event for event in events if event.get("kind") == "verification"]
    failures = [event for event in events if event.get("outcome") == "failed"]
    # A failed mutation attempt can still have changed files before failing.
    # Every definite or possible mutation therefore invalidates older checks,
    # while observed_mutations remains limited to non-failed events for the
    # separate "a change actually succeeded" completion requirement.
    last_mutation_ns = max([int(event.get("at_ns") or 0) for event in mutations] or [0])
    fresh_verifications = [
        event
        for event in verifications
        if event.get("outcome") == "success" and int(event.get("at_ns") or 0) > last_mutation_ns
    ]
    later_failed_verifications = [
        event
        for event in verifications
        if event.get("outcome") == "failed" and int(event.get("at_ns") or 0) > last_mutation_ns
    ]
    return {
        "mutations": mutations,
        "observed_mutations": observed_mutations,
        "verifications": verifications,
        "fresh_verifications": fresh_verifications,
        "later_failed_verifications": later_failed_verifications,
        "failures": failures,
    }


def validate_complete(marker, state, facts):
    errors = []
    criteria = marker.get("criteria")
    if not isinstance(criteria, list):
        errors.append("criteria must be an array")
        criteria = []
    if len(criteria) < int(state.get("minimum_criteria") or 1):
        errors.append(
            "at least %d meaningful criteria are required"
            % int(state.get("minimum_criteria") or 1)
        )

    seen = set()
    for index, criterion in enumerate(criteria):
        prefix = "criterion %d" % (index + 1)
        if not isinstance(criterion, dict):
            errors.append(prefix + " must be an object")
            continue
        criterion_id = criterion.get("id")
        if not nonempty_text(criterion_id):
            errors.append(prefix + " needs an id")
        elif criterion_id in seen:
            errors.append(prefix + " duplicates id " + str(criterion_id))
        else:
            seen.add(criterion_id)
        if not nonempty_text(criterion.get("description"), 4):
            errors.append(prefix + " needs a meaningful description")
        status = criterion.get("status")
        if status not in {"done", "waived"}:
            errors.append(prefix + " is not done or explicitly waived")
        elif status == "done" and not nonempty_text(criterion.get("evidence"), 3):
            errors.append(prefix + " needs concrete evidence")
        elif status == "waived" and not nonempty_text(criterion.get("reason"), 6):
            errors.append(prefix + " needs a specific waiver reason")

    remaining = marker.get("remaining")
    if not isinstance(remaining, list) or remaining:
        errors.append("remaining must be an empty array")
    if not nonempty_text(marker.get("summary"), 4):
        errors.append("a completion summary is required")

    verification = marker.get("verification")
    if not isinstance(verification, dict):
        errors.append("verification must be an object")
    else:
        status = verification.get("status")
        if status == "passed":
            if not facts["fresh_verifications"]:
                errors.append("no successful verification was observed after the final mutation")
            if facts["later_failed_verifications"]:
                errors.append("a verification command failed after the final mutation")
            if not nonempty_text(verification.get("summary"), 3):
                errors.append("verification needs a concrete summary")
        elif status == "not_applicable":
            if not nonempty_text(verification.get("reason"), 6):
                errors.append("not_applicable verification needs a specific reason")
        else:
            errors.append("verification.status must be passed or not_applicable")

    if facts["later_failed_verifications"]:
        failure_error = "a verification command failed after the final mutation"
        if failure_error not in errors:
            errors.append(failure_error)

    if not facts["observed_mutations"] and not nonempty_text(marker.get("no_change_reason"), 6):
        errors.append("no successful change was observed; provide a specific no_change_reason")
    return errors


def validate_disposition(marker, state, facts):
    errors = []
    if marker.get("version") != SCHEMA_VERSION:
        errors.append("marker version must be 1")
    if marker.get("task_id") != state.get("task_id"):
        errors.append("marker task_id does not match the active task")
    status = marker.get("status")
    if status == "complete":
        errors.extend(validate_complete(marker, state, facts))
        return "completed", errors
    if status == "needs_user":
        if not nonempty_text(marker.get("question"), 4):
            errors.append("needs_user requires a concrete question")
        if not nonempty_text(marker.get("why_required"), 6):
            errors.append("needs_user requires why_required")
        pending = marker.get("pending_criteria")
        if not isinstance(pending, list) or not pending:
            errors.append("needs_user requires pending_criteria")
        return "waiting_user", errors
    if status == "blocked":
        if not nonempty_text(marker.get("reason"), 8):
            errors.append("blocked requires a specific reason")
        # A review pipeline that cannot start or conclude is itself an observed
        # blocker. Its tool events are consumed by the review gate before they
        # reach the event log, so ask the gate directly instead.
        if not facts["failures"] and not review_gate.blocking_failure(state):
            errors.append("blocked requires an observed failed or denied tool event")
        return "blocked_external", errors
    errors.append("status must be complete, needs_user, or blocked")
    return "active", errors


def continuation_reason(state, errors, facts, attempt):
    # The reason is user-facing, so it stays short; the full audit findings
    # reach the model through continuation_context on the next prompt.
    if review_gate.blocking_failure(state):
        return "独立验收无法完成，请修复后重试，或用 blocked 状态说明具体原因。"
    return "还有验收项需要处理，请继续完成并核验。"


def save_audit_report(session_dir, state, errors, facts, attempt):
    report = {
        "task_id": state["task_id"], "attempt": attempt, "errors": errors,
        "changes": len(facts["observed_mutations"]),
        "verifications": len(facts["verifications"]),
        "fresh_verifications": len(facts["fresh_verifications"]),
    }
    folder = session_dir / "audits" / state["task_id"]
    atomic_write_json(folder / "latest.json", report)
    atomic_write_json(folder / ("%d.json" % time.time_ns()), report)


def continuation_context(session_dir, state):
    try:
        report = json.loads((session_dir / "audits" / state["task_id"] / "latest.json").read_text())
        errors = report.get("errors", [])
    except (OSError, ValueError):
        errors = ["Read the active task audit and repeat the relevant final verification."]
    return guard_context(state, "continuation") + "\nInternal audit findings: " + json.dumps(errors, ensure_ascii=False)


def handle_user_prompt(payload, root):
    session_dir, session_key, cwd_key = session_directory(root, payload)
    state_path = session_dir / "active.json"
    prompt = str(payload.get("prompt") or "")
    # Codex can wrap an automatic reason in hook_prompt. Only unwrap this
    # known wrapper; quoted user text must not reset or impersonate a retry.
    continuation_text = prompt
    if prompt.lstrip().startswith("<hook_prompt"):
        try:
            wrapper = ET.fromstring(prompt.strip())
            if wrapper.tag == "hook_prompt" and not list(wrapper):
                continuation_text = (wrapper.text or "").strip()
        except ET.ParseError:
            pass
    prompt_hash = digest(continuation_text)
    with locked(session_dir):
        try:
            state = read_state(state_path)
        except CorruptState:
            state = None

        if state:
            ensure_file_protocol(state, session_dir)
            stop_state = state.get("stop") or {}
            expected_hash = stop_state.get("last_reason_hash")
            is_own_continuation = prompt_hash == expected_hash or (
                continuation_text.startswith(CONTINUATION_PREFIX) and state.get("task_id") in continuation_text
            )
            if is_own_continuation:
                if state.get("phase") == "waiting_user":
                    emit(additional_context("UserPromptSubmit",
                        "The task is waiting for a user decision. This is an automatic hook message, "
                        "not a user reply or approval. Keep waiting; do not continue work or run another review."))
                    return
                state["phase"] = "active"
                write_state(state_path, state)
                emit(additional_context("UserPromptSubmit", continuation_context(session_dir, state)))
                return

        if state:
            state["audit_not_before_ns"] = time.time_ns()
            review_gate.add_user_context(state, prompt)
        classification = classify_prompt(prompt, payload.get("permission_mode"))
        if classification == "cancel":
            if state:
                state["phase"] = "cancelled"
                write_state(state_path, state)
            emit({})
            return

        if (state and state.get("phase") == "waiting_user"
                and (classification == "exempt"
                     or (classification != "enforce" and clarification_only(prompt)))):
            write_state(state_path, state)
            emit(additional_context("UserPromptSubmit",
                "The user requested explanation or read-only work, not approval to continue. "
                "Answer that request while retaining the pending user-decision checkpoint."))
            return

        if classification == "exempt" and state and state.get("phase") in {"active", "suspended"}:
            state["phase"] = "suspended"
            write_state(state_path, state)
            emit({})
            return

        if state and state.get("phase") == "waiting_user":
            state["phase"] = "active"
            state.pop("user_wait", None)
            state["stop"] = {
                "block_count": 0,
                "stalled_count": 0,
                "last_signature": None,
                "last_reason_hash": None,
            }
            write_state(state_path, state)
            emit(additional_context("UserPromptSubmit", guard_context(state, "user response received; address the reply without assuming approval of the pending step")))
            return

        if state and state.get("phase") in {"active", "suspended"} and RESUME_RE.match(prompt.strip()):
            state["phase"] = "active"
            state["stop"] = {
                "block_count": 0,
                "stalled_count": 0,
                "last_signature": None,
                "last_reason_hash": None,
            }
            write_state(state_path, state)
            emit(additional_context("UserPromptSubmit", guard_context(state, "task resumed")))
            return

        if state and state.get("phase") == "active":
            # A real user message arriving during active work is treated as an addendum.
            state["minimum_criteria"] = max(
                int(state.get("minimum_criteria") or 1), minimum_criteria(prompt)
            )
            state["stop"] = {
                "block_count": 0,
                "stalled_count": 0,
                "last_signature": None,
                "last_reason_hash": None,
            }
            write_state(state_path, state)
            emit(additional_context("UserPromptSubmit", guard_context(state, "user addendum")))
            return

        if classification == "enforce":
            state = new_state(
                payload,
                session_key,
                cwd_key,
                "explicit_execution_prompt",
                minimum_criteria(prompt),
            )
            ensure_file_protocol(state, session_dir)
            review_gate.add_user_context(state, prompt)
            write_state(state_path, state)
            emit(additional_context("UserPromptSubmit", guard_context(state, "execution request")))
            return

        if state and state.get("phase") not in TERMINAL_PHASES:
            state["phase"] = "suspended"
            write_state(state_path, state)
        emit({})


def handle_post_tool(payload, root):
    session_dir, session_key, cwd_key = session_directory(root, payload)
    state_path = session_dir / "active.json"
    kind, label = classify_tool(payload)
    newly_armed = False
    migrated = False
    with locked(session_dir):
        state = read_state(state_path)
        if state and state.get("review"):
            try:
                handled = review_gate.after_tool(state, payload)
                if handled:
                    write_state(state_path, state)
                    emit(additional_context("PostToolUse", "Review claim recorded. Continue the read-only review and return its report to the parent." if payload.get("agent_id") else review_gate.context(state)))
                    return
            except (OSError, ValueError, SnapshotError) as exc:
                state["review"]["last_error"] = str(exc)
                write_state(state_path, state)
                emit(additional_context("PostToolUse", "Independent review: " + str(exc)))
                return
        # Child tools carry the parent session_id. They are not evidence that
        # the coding task changed files or ran a successful verification.
        if payload.get("agent_id"):
            emit({})
            return
        if state and state.get("phase") in {"active", "suspended"}:
            migrated = ensure_file_protocol(state, session_dir)
            if migrated:
                write_state(state_path, state)
        if state and state.get("phase") in {"active", "suspended"}:
            kind, label = refine_guarded_bash_kind(payload, kind, label)
        if state is None and kind == "mutation":
            state = new_state(payload, session_key, cwd_key, "mutation_observed", 2)
            ensure_file_protocol(state, session_dir)
            review_gate.add_user_context(state, "")
            write_state(state_path, state)
            newly_armed = True
        elif (
            state
            and state.get("phase") == "suspended"
            and kind in {"mutation", "possible_mutation"}
        ):
            state["phase"] = "active"
            write_state(state_path, state)
            newly_armed = True

    if state and state.get("phase") == "active" and kind != "guard":
        record_event(session_dir, state, payload, kind, label)

    if newly_armed or migrated:
        emit(
            additional_context(
                "PostToolUse",
                guard_context(state, "a repository mutation automatically enrolled the task"),
            )
        )
    else:
        emit({})


def handle_stop(payload, root):
    session_dir, _, _ = session_directory(root, payload)
    state_path = session_dir / "active.json"
    with locked(session_dir):
        try:
            state = read_state(state_path)
        except CorruptState:
            fail_open("state_corrupt")
            return
        if state is None or state.get("phase") in TERMINAL_PHASES | {"suspended", "degraded", "waiting_user"}:
            emit({})
            return

        # Pausing for a human decision is a different outcome from completing
        # the work. The visible handoff wins over an accidentally submitted
        # complete audit, including its missing/stale verification evidence.
        if waiting_for_user(payload.get("last_assistant_message")):
            state["phase"] = "waiting_user"
            state["user_wait"] = {
                "source": "assistant_final",
                "message_hash": digest(payload["last_assistant_message"]),
                "at_ns": time.time_ns(),
            }
            write_state(state_path, state)
            emit({})
            return

        stop_state = state.setdefault(
            "stop",
            {
                "block_count": 0,
                "stalled_count": 0,
                "last_signature": None,
                "last_reason_hash": None,
            },
        )
        events = read_events(session_dir, state["task_id"])
        facts = evidence_summary(events)
        marker, marker_error, marker_hash = read_audit(state, facts, payload.get("last_assistant_message"))
        if marker_error:
            disposition = "active"
            errors = [marker_error]
        else:
            disposition, errors = validate_disposition(marker, state, facts)
            if disposition == "completed":
                errors.extend(review_gate.completion_errors(state))

        if not errors and disposition in {"completed", "waiting_user", "blocked_external"}:
            state["phase"] = disposition
            state["completion_marker_hash"] = marker_hash
            write_state(state_path, state)
            emit({})
            return

        save_audit_report(session_dir, state, errors, facts, int(stop_state.get("block_count") or 0) + 1)
        event_signature = [
            (event.get("event_id"), event.get("kind"), event.get("outcome")) for event in events
        ]
        signature = digest({"marker": marker_hash, "events": event_signature, "errors": errors})
        block_count = int(stop_state.get("block_count") or 0)
        stalled_count = int(stop_state.get("stalled_count") or 0)
        if signature == stop_state.get("last_signature"):
            stalled_count += 1
        else:
            stalled_count = 0

        if block_count >= MAX_BLOCKS or stalled_count >= 2:
            state["phase"] = "degraded"
            state["degraded_reason"] = (
                "max_blocks" if block_count >= MAX_BLOCKS else "no_observable_progress"
            )
            if state.get("review"):
                state["review"]["completion_status"] = "unverified"
            stop_state["stalled_count"] = stalled_count
            write_state(state_path, state)
            fail_open(state["degraded_reason"])
            return

        attempt = block_count + 1
        reason = continuation_reason(state, errors, facts, attempt)
        stop_state["block_count"] = attempt
        stop_state["stalled_count"] = stalled_count
        stop_state["last_signature"] = signature
        stop_state["last_reason_hash"] = digest(reason)
        write_state(state_path, state)
        emit({"decision": "block", "reason": reason})


def handle_interrupt(payload, root):
    session_dir, _, _ = session_directory(root, payload)
    state_path = session_dir / "active.json"
    with locked(session_dir):
        try:
            state = read_state(state_path)
        except CorruptState:
            emit({})
            return
        if state and state.get("phase") == "active":
            state["phase"] = "suspended"
            write_state(state_path, state)
    emit({})


def handle_review_event(payload, root):
    session_dir, _, _ = session_directory(root, payload)
    state_path = session_dir / "active.json"
    with locked(session_dir):
        state = read_state(state_path)
        if not state or not state.get("review"):
            emit({})
            return
        event = payload["hook_event_name"]
        if event == "PreToolUse":
            try:
                denial = review_gate.before_tool(state, payload)
            except (OSError, ValueError, SnapshotError) as exc:
                denial = "独立验收尚未就绪：" + str(exc)
            write_state(state_path, state)
            if denial:
                emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                      "permissionDecision": "deny", "permissionDecisionReason": denial}})
            else:
                emit({})
            return
        if event == "SubagentStart":
            review_gate.subagent_start(state, payload)
        elif event == "SubagentStop":
            review_gate.subagent_stop(state, payload, session_dir)
        write_state(state_path, state)
    emit({})


def run_hook():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise GuardError("input_not_object")
        event_name = payload.get("hook_event_name")
        root = plugin_data_root()
        if event_name == "UserPromptSubmit":
            handle_user_prompt(payload, root)
        elif event_name == "PostToolUse":
            handle_post_tool(payload, root)
        elif event_name == "Stop":
            handle_stop(payload, root)
        elif event_name == "Interrupt":
            handle_interrupt(payload, root)
        elif event_name in {"PreToolUse", "SubagentStart", "SubagentStop"}:
            handle_review_event(payload, root)
        else:
            emit({})
    except (GuardError, OSError, ValueError, TypeError, SnapshotError):
        fail_open("hook_runtime_error")


def submit_audit(filename):
    path = Path(filename).expanduser().absolute()
    allowed = Path(tempfile.gettempdir()).resolve() / "codex-task-completion-guard"
    resolved = path.resolve()
    if allowed not in resolved.parents or not re.fullmatch(r"tcg_[0-9a-f]+\.json", path.name):
        raise GuardError("invalid_audit_path")
    raw = sys.stdin.buffer.read(MAX_MARKER_BYTES + 1)
    if len(raw) > MAX_MARKER_BYTES:
        raise GuardError("audit_too_large")
    audit = json.loads(raw)
    if not isinstance(audit, dict) or audit.get("task_id") != path.stem or audit.get("version") != SCHEMA_VERSION:
        raise GuardError("invalid_audit_identity")
    atomic_write_json(path, audit)
    sys.stdout.write("Completion audit saved locally.\n")


def main(argv):
    if len(argv) >= 2 and argv[1] in {"prepare-review", "review-claim"}:
        try:
            if len(argv) == 4 and argv[1:3] == ["prepare-review", "--audit-file"]:
                raw = sys.stdin.buffer.read(MAX_MARKER_BYTES + 1)
                if len(raw) > MAX_MARKER_BYTES:
                    raise ValueError("preparation exceeds 64 KiB")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("preparation must be a JSON object")
                result = review_gate.prepare(argv[3], data, os.getcwd())
            elif len(argv) == 6 and argv[1:3] == ["review-claim", "--audit-file"] and argv[4] == "--run-id":
                result = review_gate.claim(argv[3], argv[5])
            else:
                raise ValueError("invalid independent review command")
            emit(result)
            return 0
        except (OSError, ValueError, KeyError, TypeError, SnapshotError) as exc:
            sys.stderr.write("Independent review unavailable: %s\n" % exc)
            return 2
    if len(argv) == 4 and argv[1:3] == ["submit", "--audit-file"]:
        try:
            submit_audit(argv[3])
        except (GuardError, OSError, ValueError, TypeError):
            sys.stderr.write("Unable to save completion audit; check path and JSON.\n")
            return 2
        return 0
    if len(argv) != 2 or argv[1] != "hook":
        sys.stderr.write("usage: completion_guard.py hook | submit --audit-file PATH\n")
        return 2
    run_hook()
    return 0


if __name__ == "__main__":
    os.umask(0o077)
    sys.exit(main(sys.argv))
