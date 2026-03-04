# PyCharm Git Log Stale State — Recovery & Prevention

## What happens

When `git commit` runs outside PyCharm (e.g. via Claude Code / bash), PyCharm's Git log
view may not refresh to show the new commit. The tab title shows `Log: <old-ref>` instead
of `Log: main`, and the branch tip appears stale. The commit exists in git — PyCharm's
in-memory model just hasn't re-resolved where the branch pointer moved.

PyCharm does perform a periodic background refresh and will pick up the new commit on its
own after a short delay. Manual recovery steps below are only needed if you can't wait.

## Recovery (fastest first)

1. **Ctrl+Alt+Y** — Synchronize / Refresh. Forces PyCharm to re-scan `.git`. Try this first.
2. **Git log refresh button** — circular arrow in the Git log toolbar. Same effect.
3. **VCS > Git > Fetch** — heavier, but reliably clears all stale ref state.
4. **Checkout dance** — checking out another branch and back forces a full VCS model reload.
   Works, but is more disruptive than needed.

## Prevention

- Prefer **Ctrl+Alt+Y immediately after any external commit** as a habit.
- Or use PyCharm's built-in VCS commit dialog — PyCharm updates its model atomically
  when it drives the commit itself.

## GUI operations

Git push via PyCharm's GUI worked normally following an external commit (after the log had
refreshed).

## Confirmation without PyCharm

If the log looks wrong, verify the commit landed correctly before doing anything:

```bash
git log --oneline -5
```

If the hash and message are right, the issue is cosmetic — PyCharm will catch up.
