# provenance/

This directory captures engineering provenance for the repository — records of how
work was done, not just what changed. Git commit history shows the diff; provenance
shows the reasoning, the prompting, and the decisions that produced it.

## conversations/

Each file documents a Claude Code session, committed alongside or shortly after
the code changes it produced.

**Naming convention:** `YYYYMMDD-HHMMSS_<desc>.md`

The description matches or closely references the associated commit.

**File structure:**

Each file has two sections:

*Summary* — a structured lead covering what was built or changed and why, key
decisions made, and the arc of the session. Readable in isolation for a quick
orientation.

*Transcript* — a turn-by-turn record of the session. User messages are reproduced
in full (pasted or non-authored content is replaced with a short descriptor); assistant
responses are summarised to one paragraph per turn. This section preserves the
steering behaviour, framing, and context that shaped the work — information that
exists nowhere else in the repository.
