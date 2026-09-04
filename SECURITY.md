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

The plugin stores task state beneath the Codex-provided `PLUGIN_DATA`
directory. It records event classifications, timestamps, outcomes, and SHA-256
hashes. It is designed not to persist raw prompts, tool inputs, or tool outputs,
and it does not send telemetry or task data to a remote service.

The guard fails open on internal errors and after its bounded retry limit. It is
a workflow guardrail, not a security sandbox or a formal proof that an
implementation is correct.
