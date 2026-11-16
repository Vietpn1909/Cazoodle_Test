---
name: update-plan
description: Update project progress and decisions — tick checklist items in .claude/docs/plan.md, add ADRs to decisions.md, and refresh CLAUDE.md commands/facts. Use at the end of a work session or when a decision changes.
---

# Update plan & decisions

1. Inspect what changed (git diff/status if a repo, otherwise recently modified files).
2. In `.claude/docs/plan.md`: tick completed items, add short notes for scope changes, add newly discovered tasks under the right milestone, update "Open questions".
3. If a design choice was made or changed, append an ADR to `.claude/docs/decisions.md` (context → decision → consequences); mark superseded ADRs instead of deleting.
4. If commands, stack or verified facts changed, update the matching section of `.claude/CLAUDE.md` (keep it concise; details belong in `.claude/docs/`).
5. Summarize to the user: done, next 3 tasks, blockers.
