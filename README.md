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
  verification events; and
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

In the new task or session, enter `/hooks`, review the four hooks from Task
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

The plugin listens to four lifecycle events:

| Event | Purpose |
| --- | --- |
| `UserPromptSubmit` | Classify the request and inject the completion protocol for execution work. |
| `PostToolUse` | Record hashed evidence about file mutations, shell mutations, verification commands, and their outcomes. |
| `Stop` | Validate the structured completion declaration and block an incomplete finish. |
| `Interrupt` | Suspend the active guard when the user explicitly interrupts the task. |

For an active task, Codex must declare:

- meaningful acceptance criteria derived from the entire request;
- a status and concrete evidence for every criterion;
- an empty remaining-work list;
- verification performed after the final observed mutation, or a specific
  reason why verification is not applicable;
- a concrete question when a real user decision is required; or
- a specific external blocker backed by an observed failed or denied tool
  event.

If the audit fails, the Stop hook emits a blocking decision with the missing
items and Codex gets another opportunity to continue. Blocking is deliberately
bounded to three attempts, and repeated runs without observable progress
degrade to fail-open behavior rather than trapping the session forever.

### Data and privacy

The hook runs locally and does not send telemetry or task data to a remote
service.

State is written beneath the Codex-provided `PLUGIN_DATA` directory. The
plugin stores classifications, timestamps, outcomes, and SHA-256 hashes. It is
designed not to retain raw prompts, tool inputs, or tool outputs, and it does
not parse the unstable Codex transcript format.

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
- A nonempty evidence description is not independently verified as a semantic
  claim.
- Internal errors and exhausted retry limits fail open.

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
