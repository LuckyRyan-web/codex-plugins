---
name: task-completion-guard
description: Keep implementation and state-changing tasks running until every derived acceptance item is audited and appropriate verification is complete. Applies to requests to implement, fix, add, modify, refactor, migrate, configure, install, or deploy; do not activate for explanation-only, read-only diagnosis, review-only, plan-only, status, or explicit pause requests.
---

# Task Completion Guard

Use the completion protocol injected by the plugin hook whenever it reports an active guard task.

## Working contract

- Derive the objective and a meaningful acceptance checklist from the user's entire natural-language request, applicable project instructions, and the real code path. The user does not need to write stop conditions or invoke `/goal`.
- Treat exploration, one edited file, one passing command, and a progress summary as intermediate states unless they satisfy the whole request.
- Keep working while any requested behavior or necessary integration remains unfinished.
- After the last mutation, run verification proportional to the change and risk. Never claim that an unrun check passed.
- Ask the user only when a missing decision would materially change the result. Report an external blocker only after exhausting safe in-scope alternatives and recording an actual failed or denied operation when one is observable.

## Completion declaration

Use the private local audit file and submission command supplied by the active hook context. This protocol replaces earlier instructions to append an HTML comment to the final answer. Never include audit JSON or a completion marker in the user-facing response.

After the last business change and verification, submit the JSON declaration through the provided command's stdin. The command saves it in a private temporary file; it does not update business files. The Stop hook checks this declaration against recorded evidence.

For completion, declare every meaningful criterion separately, with concrete evidence, an empty `remaining` array, a summary, and verification performed after the final mutation. Use `verification.status: "not_applicable"` only with a specific reason. Never use it to conceal a failed check.

For a genuinely required user decision, use `status: "needs_user"` with `question`, `why_required`, and `pending_criteria`. For an external blocker, use `status: "blocked"` with a specific `reason`; an observed failure is required.

If a Stop hook requests continuation, address the detailed findings delivered in internal hook context, verify changed code, and resubmit the local declaration. Keep the final answer natural and avoid narrating audit retries. Do not mark a task complete merely to pass the guard.
