# LuckyRyan Codex Plugins

[简体中文](README.zh-CN.md)

A repository marketplace for Codex plugins maintained by
[LuckyRyan-web](https://github.com/LuckyRyan-web).

## Task Completion Guard

Task Completion Guard helps Codex carry implementation and state-changing tasks
through a structured completion audit instead of ending after an intermediate
step or a progress update.

It combines:

- a skill that tells Codex to derive acceptance criteria from the complete
  natural-language request;
- lifecycle hooks that enroll execution tasks and observe local change and
  verification events;
- an independent reviewer that checks a fixed snapshot without inheriting the
  coding conversation; and
- a Stop hook that blocks a premature finish and returns concrete audit gaps to
  Codex.

The user does not need to define stop conditions or invoke `/goal`.

> [!IMPORTANT]
> This plugin executes a local Python hook. Codex will not run the hook until
> you review and trust its exact definition with `/hooks`.

### Requirements

- A current Codex build with plugin and lifecycle-hook support.
- Python 3 available as `python3`.
- macOS or Linux for the currently tested command path.

Release 0.1.0 was tested with Codex CLI 0.153.0-alpha.5 and Python 3.14 on
macOS. The test suite also runs against Python 3.9, 3.11, and 3.13 on Linux in
CI. Windows support is not yet claimed because the Windows hook command has not
been verified.

### Install

Add this GitHub repository as a Codex marketplace:

```bash
codex plugin marketplace add LuckyRyan-web/codex-plugins --ref main
```

Install the plugin:

```bash
codex plugin add task-completion-guard@luckyryan-codex-plugins
```

Then pick the matching surface:

- **Codex desktop app:** restart the app and open a new task.
- **Codex CLI:** exit any session that was already running, then start a new
  `codex` session.

In the new task or session, enter `/hooks`, review the seven hooks from Task
Completion Guard, and trust them.

To pin the marketplace itself to the first release instead of following
`main`, use:

```bash
codex plugin marketplace add LuckyRyan-web/codex-plugins --ref v0.1.0
```

### Use

Use Codex normally. Requests such as these enroll automatically:

```text
Implement this feature completely and verify the result.
Fix the login bug, add regression coverage, and update the documentation.
把这个功能完整实现，并完成必要验证。
```

Explanation-only, read-only analysis, review-only, plan-only, and explicit
pause requests are exempt.

To opt out for one request, include:

```text
[completion-guard:off]
```

### How it works

The plugin listens to seven lifecycle events:

| Event | Purpose |
| --- | --- |
| `UserPromptSubmit` | Classify the request and inject the completion protocol for execution work. |
| `PreToolUse` | Check the exact review handoff, `fork_turns: "none"`, attempt limit, and snapshot; restrict tools for the bound reviewer. |
| `PostToolUse` | Record the parent's change and verification evidence; observe review preparation, spawn acknowledgement, and the child's identity claim. |
| `SubagentStart` | Record the child identity supplied by the host. |
| `SubagentStop` | Collect the actual review report from that child's final message. |
| `Stop` | Validate the structured completion declaration and block an incomplete finish. |
| `Interrupt` | Suspend the active guard when the user explicitly interrupts the task. |

Before a new task completes, Codex runs the hook-supplied `prepare-review`
command with acceptance requirements, file paths, risk, and actual verification
evidence on stdin. It saves a private request, file copies, diff, and digests;
it does not call a model. Git snapshots include every dirty and untracked file,
including existing changes; explicit `paths` cannot exclude them. Non-Git
directories require explicit file paths.

The captured scope and `risk` determine whether an independent review is needed:

| Condition | Independent review |
| --- | --- |
| `risk: "important"`, or a critical path match such as authentication, permissions, payments, or migrations | Required. |
| `risk: "routine"` | Reserved for tiny, low-risk wording or formatting changes. Exempt only at no more than 1 file and 20 changed lines; critical paths still require review. |
| Default `risk: "auto"` | Required for code/configuration files, more than 2 files, or more than 80 changed lines. |

Changed lines count additions plus deletions; non-Git files count their full
contents. Critical is a path classification, not a fourth `risk` value. Use
`important` for business logic, permissions, data, or consequential behavior.
Do not label such work `routine` to skip review.

When required, the parent passes the returned `spawn` fields **unchanged** to
the native `spawn_agent` tool, explicitly using `fork_turns: "none"`. It creates
a fresh child context without copying the coding chat or creating a separate
user-facing task. The child first runs the exact `review-claim` command in the
handoff. Hooks bind the review to its host-provided `agent_id`, then collect the
actual report from `SubagentStop.last_assistant_message`. A parent-authored
claim that review passed is not review evidence.

The bound reviewer reads the generated `diff.patch`, snapshot, and necessary
dependencies with allowed commands such as `cat` and `rg`. It does not run Git
commands directly, because repository content filters can execute programs.

Each task allows at most two independent reviews: the initial review and one
review after fixes. Fix findings, verify, and prepare again only after code or
requirements change. Do not repeatedly retry capacity or quota errors. For a
task requiring review, completion requires every criterion to pass, no remaining
findings, and the reviewed snapshot and user requirements to remain current.
An unfinished review must be reported as unverified.

Finally, Codex submits a JSON declaration through the hook-supplied `submit`
command's stdin. The command writes a private local audit file; the user-facing
answer contains no audit JSON or completion marker. Submit it after the final
business change, verification, and any required independent review, including:

- meaningful acceptance criteria derived from the entire request;
- a status and concrete evidence for every criterion;
- an empty remaining-work list;
- verification performed after the final observed mutation, or a specific
  reason why verification is not applicable;
- a concrete question when a real user decision is required; or
- a specific external blocker backed by an observed failed or denied tool
  event.

If the audit fails, the Stop hook blocks with a short visible continuation
message. It saves detailed findings locally and supplies them to Codex through
internal hook context on continuation. Blocking is deliberately bounded to
three attempts, and repeated runs without observable progress degrade to
fail-open behavior rather than trapping the session forever. This is separate
from the two-review limit; a fail-open release is not a passing review.

Already-active tasks from older versions without a `review` state retain their
existing completion protocol. New tasks use the review workflow above.

### Data and privacy

The Python scripts run locally without making model requests or sending
telemetry. The independent child normally calls the current Codex model and
consumes account usage. The handoff's user requirements, and code or evidence
the child reads, enter that child's model context. Model overrides are omitted
by default.

Task state, event records, and audit diagnostics are written beneath
`PLUGIN_DATA/completion-guard/v1/sessions/<session-hash>/`. They include task
metadata, the audit-file path, event classifications, timestamps, outcomes,
SHA-256 hashes, diagnostic errors, and verification counts. Routine event
records hash prompt and tool data. **Independent review also retains raw user
requests, requirements, verification descriptions, diffs, file copies, and child
reports**, so plugin data is not limited to hashes. Review requests in task
state and accepted reports are retained in this private data directory. The
hook does not parse the Codex transcript format.

The submitted JSON declaration is stored separately beneath the system
temporary directory at
`codex-task-completion-guard/<session-hash>/<task-id>.json`. It contains the
model's acceptance criteria, evidence descriptions, summary, and any stated
question or blocker, so it can contain task-related text. Adjacent
`.context.json`, `.review-request.json`, and `.review-<run-id>/` artifacts retain
raw requirements, the request, and the snapshot. On POSIX systems, new private
directories and writable JSON files use `0700` and `0600`; saved snapshot
directories and files use `0500` and `0400`. These records are not automatically
deleted on completion or interruption.

See [SECURITY.md](SECURITY.md) for the reporting process and security model.

### Limitations

Task Completion Guard is a workflow guardrail, not a formal proof of
correctness.

- Natural-language enrollment and command classification use conservative
  heuristics and can produce false positives or false negatives.
- The model still performs the semantic decomposition of the request. The hook
  validates the declaration and observable evidence but cannot prove that every
  business requirement was understood correctly.
- Hosted or specialized tools may not traverse the local tool-hook path.
- Independent model review can still miss defects and cannot replace checks
  that were never run.
- Reviewer read restrictions use prompts and hooks, not a promised operating
  system sandbox. Local users or malicious processes can alter scripts or
  state; the plugin is not a boundary against adversarial behavior.
- Snapshot limits, unsafe file reads, or missing evidence cannot establish a
  passing review.
- Internal errors and exhausted retry limits fail open. Unfinished independent
  review must still be reported as unverified.

### Update

Refresh the marketplace and reinstall the plugin after a newer version is
published:

```bash
codex plugin marketplace upgrade luckyryan-codex-plugins
codex plugin add task-completion-guard@luckyryan-codex-plugins
```

Open a new task after reinstalling. If the Hook definition changed, review its
new hash with `/hooks`.

### Uninstall

```bash
codex plugin remove task-completion-guard@luckyryan-codex-plugins
codex plugin marketplace remove luckyryan-codex-plugins
```

Removing the marketplace is optional if you want to keep access to other
plugins that may be added to this repository later.

## Development

Run all repository tests:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
python3 -m unittest discover \
  -s plugins/task-completion-guard/tests \
  -p "test_*.py"
```

The plugin intentionally uses only the Python standard library.

## Versioning

Plugin versions follow semantic versioning. See [CHANGELOG.md](CHANGELOG.md)
for release notes.

## License

[MIT](LICENSE)
