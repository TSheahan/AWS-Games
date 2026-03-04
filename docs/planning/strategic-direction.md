# AWS-Games Strategic Direction

Last reviewed: 2026-03-05

---

## Top-level goals (refined)

1. **Functional:** Run Minecraft servers on AWS cost-effectively. The server works and is
   actively used. The worst pain points were resolved in prior sessions.

2. **Portfolio:** Enterprise-grade infrastructure tooling (CloudFormation, SSM, boto3,
   systemd, production EBS/NVMe handling) developed through agentic co-work with Claude
   Code and designed for agent-driven operation. `README.md` is the primary external
   entry point.

3. **Extensibility:** `ec2/<game>/` structure is deliberately game-agnostic. A shared
   orchestration layer (`ec2/setup.sh`) is deferred until a second game type materialises —
   no speculative abstraction.

---

## Deliberate constraints

**Portfolio build-out is cost- and effort-constrained.** The server is already a great
source of multiplayer fun. Infinite gold-plating has diminishing returns. New work should
pass a "real value" test — either operational usefulness or a meaningfully distinct
portfolio signal. Don't extend for its own sake.

---

## Current development resolution

### Priority 1 — Build agentic operational patterns

*(Priority 0 was validating the provisioning chain end-to-end — completed 2026-03-04.
See CHANGELOG.md for the full account. Priority 1 is now unblocked.)*

Claude Code as the control plane for routine infrastructure ops. Natural-language commands
("start the game server", "check server status", "redeploy with version X") map to
tested, reliable execution sequences.

The agentic layer is only credible once the underlying workflows are validated — which they
now are (see CHANGELOG.md, 2026-03-04).

**Shape of the agentic layer (emerging):**
- Operational runbooks in a Claude-executable form (operations.md is a start)
- Named playbooks — discrete, tested task sequences that map to natural-language intents
- Precondition checks before each operation (is the stack up? volume available? SSH reachable?)

**Open design question — operational knowledge architecture:**

The current private memory (`memory/operations.md`) mixes two categories with different
durability needs:

- **Personal workstation state:** key paths, current volume ID, active run config parameters.
  This is correctly private — it changes, it's machine-specific.
- **Operational procedures:** SSH commands, systemd patterns, log locations, provisioning
  workflows. These are purely about the system, not the workstation. They're derivable from
  the repo and equally valid on any machine.

An agentic layer that depends on private memory being loaded correctly is fragile. Procedures
should be in source-controlled CLAUDE.md (auto-loaded in every session, every machine). Only
personal state belongs in private memory.

**Prerequisite:** do the split first. Migrate procedures into the project CLAUDE.md (or a
dedicated `docs/runbook.md` if volume warrants it). Shrink `operations.md` to personal state
only. Playbooks that exist only in private memory aren't really playbooks.

---

## Secondary tracks (valid, pick up after Priority 1)

- Track B — Config-as-code discipline (JSON Schema for `minecraft-servers.yaml`)
- Track C — CI (GitHub Actions; Track A prereq is done)
- Track D — Developer experience (`.env` support, Makefile)
