# AWS-Games Strategic Direction

Last reviewed: 2026-03-08

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
See CHANGELOG.md for the full account.)*

Claude Code as the control plane for routine infrastructure ops. Natural-language commands
("start the game server", "check server status", "redeploy with version X") map to
tested, reliable execution sequences.

**Progress within Priority 1 (2026-03-08):**

- `bin/instance.py` — workstation-side instance control; resolves the current stack at call
  time, so OS shortcuts survive reinstalls without modification
- Mobile control API — Lambda Function URL for start/stop from Android home screen shortcuts;
  no credential management required (capability URL pattern)
- **Operational knowledge split** — resolved 2026-03-08. `memory/operations.md` previously
  mixed personal state with system procedures; procedures migrated into the CLAUDE.md
  hierarchy (source-controlled, auto-loaded on every machine). `operations.md` now holds
  personal state only: active volume ID, ports, jar URL, redeployment runbook with real
  values. Playbooks are now viable — their prerequisites are in source-controlled context
  rather than private memory that may or may not be loaded.
- **Persistent EIP + EBS stack** — resolved 2026-03-08. `GamePersistentStack` owns the EBS
  volume and EIP; game stack is now fully stateless. Reinstalls no longer affect the IP or
  world data. End-to-end rebuild verified with world data intact. See CHANGELOG.md.

**What remains:**
- Named playbooks — discrete, tested task sequences that map to natural-language intents;
  provisioning log retrieval is the natural first one (stack creates → SSH in → retrieve
  logs → surface failures)
- Precondition checks before each operation (is the stack up? volume available? SSH reachable?)

---

## Secondary tracks (valid, pick up after Priority 1)

- Track B — Config-as-code discipline (JSON Schema for `minecraft-servers.yaml`)
- Track C — CI (GitHub Actions; Track A prereq is done)
- Track D — Developer experience (`.env` support, Makefile)
