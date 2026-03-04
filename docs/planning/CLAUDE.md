# docs/planning/ — How This Directory Works

Tracked, public planning content. Forward-looking: strategic direction, side-quests,
in-flight decisions, and carry-forward notes from working sessions.

This directory is part of the public repo. Keep content project-relevant. Workstation-
specific operational notes (SSH runbooks, active volume IDs, current IP) belong in private
memory (`~/.claude/projects/.../memory/`), not here.

---

## Relationship to CHANGELOG.md

`CHANGELOG.md` (repo root) is the "done" destination — completed work lands there.
`docs/planning/` holds what is upcoming or in-flight. The two form a coherent pair:
planning records intent; the changelog records outcomes.

When work from a planning file is complete, summarise it in CHANGELOG.md and delete or
trim the planning file.

---

## File naming convention

### Date-prefixed: `YYYY-MM-DD_short-slug.md`

Use when the content is **time-bound within its context** — typically session wrap-ups
that capture what was done, what's open, and how to resume. These are useful for a few
sessions after they're written, then their relevance fades.

Delete once absorbed into CHANGELOG.md or promoted to a durable planning file.

Example: `2026-03-03_strategic-direction.md`

### No date prefix: `short-slug.md`

Use when the content **retains relevance over time** — strategic direction, side-quests,
or durable planning content that will still be correct and useful weeks or months later.
Update in place rather than creating dated copies.

Example: `strategic-direction.md`

---

## Relation to private memory (`~/.claude/projects/.../memory/`)

Private memory holds personal operational state: active volume IDs, current instance IP,
SSH runbooks, and distilled stable knowledge Claude should carry into every session.
`docs/planning/` holds project intent — what is planned, why, and what trade-offs were
considered. The two complement each other; neither replaces the other.
