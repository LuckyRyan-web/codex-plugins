# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

## Reporting a vulnerability

Please use GitHub's private **Report a vulnerability** flow in the Security tab
of this repository. Do not include secrets, private prompts, proprietary source
code, or other sensitive data in a public issue.

Include the affected plugin version, Codex version, operating system, a minimal
reproduction, and the impact. You should receive an initial response within
seven days.

## Local execution and data handling

Task Completion Guard contains lifecycle hooks that execute a bundled Python
script on the user's machine. Codex requires users to review and trust the hook
definition before it can run.

The plugin stores task state and event records beneath
`PLUGIN_DATA/completion-guard/v1/sessions/<session-hash>/` (with
`CLAUDE_PLUGIN_DATA` as a fallback when `PLUGIN_DATA` is unset or empty). State
includes task metadata and the audit-file path. Event records include tool
names, classifications, labels, timestamps, outcomes, and SHA-256 hashes of
tool inputs and outputs; prompt text is also hashed. Failed audits produce
reports under `audits/<task-id>/` containing diagnostic errors and change and
verification counts. Detailed findings are supplied to Codex through internal
hook context; the visible continuation message is brief.

Codex submits the declaration as JSON through the hook-provided command's
stdin. It is stored separately under the system temporary directory at
`codex-task-completion-guard/<session-hash>/<task-id>.json`. This file retains
the model's acceptance criteria, evidence descriptions, summary, and any
question or blocker. Those fields can contain task-related text even though
automatic event recording does not retain raw prompts or tool inputs and
outputs. The final user-facing answer must not contain this audit JSON or a
completion marker.

On POSIX systems, new private directories use permissions `0700`, and JSON
files use `0600`.
The plugin does not automatically expire or delete state, event records,
diagnostic reports, or audit declarations on completion or interruption. It
does not parse the Codex transcript format or send telemetry or task data to
a remote service.

The guard fails open on internal errors and after its bounded retry limit. It is
a workflow guardrail, not a security sandbox or a formal proof that an
implementation is correct.
