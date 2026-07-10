# CLAUDE.md

Autonomous coding conventions for this repo. Be terse: no narration, no pleasantries, just output.

## Model routing (cost discipline)
This session is the ORCHESTRATOR: plan, delegate, review. Don't write bulk code or read large files/logs here — keep this context lean.
- Implementation → `implementer` subagent (Sonnet).
- Boilerplate / tests / mocks / codebase search / smoke+build checks → `grunt` subagent (Haiku).
- Final diff review → `reviewer` subagent.
Delegate only for multi-file or heavy tasks; do trivial edits inline (orchestration overhead isn't worth it on small changes). Workers return summaries, never full logs/diffs. Escalate a worker to Opus/Fable manually only for a genuinely hard subproblem.

## Plan-first gate (not skippable)
Multi-file or systemic change → write `plan.md`, stop, get user confirmation before touching code. Delete `plan.md` after execution.

## PROGRESS.md (root state file)
Read at session start (if `Status: STALE` or `Exit: error/interrupted`, verify before trusting). Update after each feature/fix and before ending. Create from `PROGRESS.template.md` if missing. Keep dense and factual.

## On failure
Stop. Set PROGRESS.md `Exit: error`, log the failure + rollback target. Report what failed, files touched, rollback target. No multi-file self-repair without a new plan cycle.

## Research & editing
- Grep/find/ls before reading; confirm relevance first.
- Read only the functions/sections needed, not whole files.
- Targeted diffs, not full-file rewrites.
- Max 3 files in context unless authorized.

## Code standards
- No placeholders (`// rest of code...`) unless asked — complete snippets for changed functions.
- Verify small blocks before scaling across files.
