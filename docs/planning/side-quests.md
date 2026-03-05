# Side Quests

Interesting, self-contained problems worth solving — not on the critical path but with
distinct value in the solving. Pick up opportunistically.

---

## Enhanced /usage view

**Pain point:** `/usage` shows current consumption but gives no sense of how far the weekly
reset is. A bar graph visualising time-to-reset would make the remaining budget immediately
readable.

**Complication:** `/usage` is an interactive command — it waits for user input and isn't
scriptable as-is.

**Approach:**

1. **Prior art check first.** Search for existing community solutions, shell aliases, or
   Claude Code plugin patterns that already solve or approximate this. No need to invent if
   it exists.

2. **If no prior art:** Design and implement. Options include:
   - A custom slash command (Claude Code skill/hook) that surfaces the enhanced view
   - A standalone script (`bin/usage.sh` or similar) that wraps or replaces the raw `/usage`
     output with a rendered bar graph
   - A combination: script does the computation, slash command is the ergonomic entry point

   There's distinct value in proving out the capability regardless — custom slash commands
   and usage tooling are portfolio-adjacent demonstrations of Claude Code extensibility.

**Open design questions:**
- Where does reset-time data come from? Is it exposed via Claude Code internals, the API,
  or does it need to be inferred/tracked locally?
- Bar graph in terminal (ASCII) or something richer?
- Scope: AWS-Games-specific or a global (`~/.claude/`) tool?

---

## Persistent Elastic IP across stack rebuilds

**Pain point:** The EIP is currently owned by the CloudFormation stack. When the stack is deleted and recreated, the public IP changes — players need to be told the new address, and any DNS or saved server entries break.

**Approach:** Allocate the EIP once outside CloudFormation as a standalone resource (a manual `aws ec2 allocate-address` or a separate single-resource stack with `DeletionPolicy: Retain`). Pass the Allocation ID into the game stack as a parameter and use `AWS::EC2::EIPAssociation` instead of `AWS::EC2::EIP`. The EIP survives stack deletion; the instance just re-associates on each new deploy.

**Design questions:**
- One-off manual allocation or a minimal "persistent resources" stack that holds both the EIP and the EBS volume? A shared persistent stack is cleaner if the pattern is repeated.
- `reinstall_stack.py` already handles `ExistingVolumeId` as a pass-through parameter — `ExistingAllocationId` would follow the same pattern with the same reuse logic.
- Does this warrant a corresponding `GAME_EXISTING_ALLOCATION_ID` env var, or is CLI-only sufficient?

---

## `minecraft` admin wrapper command

**Vision:** A single `minecraft` command on the EC2 instance that wraps all common admin
operations, with bash autocomplete support. Installed into ec2-user's PATH at provision time.

**Subcommand surface (target):**
```
minecraft status [--yaml]
minecraft status <instance> [--yaml]
minecraft stop
minecraft stop <instance>
minecraft start
minecraft start <instance>
minecraft screen <instance>
minecraft reprovision
```

**Implementation notes:**
- `status` implemented inline in the wrapper (absorbs and fixes `mcstatus.sh` logic)
- Other subcommands call out to existing generated scripts / systemctl
- Install to `/home/ec2-user/bin/minecraft` (on PATH already; `setup.sh` must `mkdir -p` it first)
- `setup.sh` handles installation so the command is available from first login
- Bash autocomplete via a completion script — research needed on the right mechanism for
  AL2023 (likely a `/etc/bash_completion.d/` drop-in or `~/.bash_completion`)

**`mcstatus.sh` bug (pre-existing, absorbed into wrapper):**
Script calls `systemctl is-active` per unit. `is-active` exits non-zero for inactive
services, so `|| echo "unknown"` fires inside the command substitution — producing both
the real status text and a spurious `unknown` line per inactive unit. Fix: `|| true` +
separate empty-check.

**PATH research (ec2-user, 2026-03-05):**
- `echo $PATH` → `/home/ec2-user/.local/bin:/home/ec2-user/bin:/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin`
- `/home/ec2-user/bin` is on PATH but does not exist yet — `setup.sh` must create it
- `/home/ec2-user/.local/bin` also absent; skip for now, `/home/ec2-user/bin` is sufficient
- Prefer ec2-user ownership over `/usr/local/bin` (no sudo needed to update)

**Resolved design decisions:**
- Bash completion: `/etc/bash_completion.d/minecraft` drop-in written by `setup.sh` (root context; system-wide; sourced automatically)
- `reprovision` and `start`/`stop`: use `sudo`; ec2-user has passwordless sudo on AL2023 so no sudoers entry needed
- `screen <instance>`: guards against no active session with a clear error message
- Wrapper is in `ec2/minecraft/minecraft`; completion in `ec2/minecraft/minecraft-completion.bash`; both installed by `setup.sh`

**Still to verify on next live instance:**
- Completion fires correctly after boot (bash-completion package present and sources `/etc/bash_completion.d/`)
- `🟢 running` status path for a live `vanilla` instance
- `minecraft screen vanilla` attaches cleanly

---

## Provisioning log retrieval

**Value:** After a `reinstall_stack.py` cycle completes, the EC2 instance holds the
authoritative record of what happened: UserData output (`/var/log/cloud-init-output.log`)
and any output captured from `setup.sh` and `provision_servers.py`
(`/var/log/minecraft-provision.log`). Pulling these back to the workstation after a deploy
would make verification and debugging significantly faster than SSH-and-scroll.

**Connection to agentic patterns:** This is a natural post-provision step for an agent —
stack creates, IP is known, SSH in, retrieve logs, surface any failures. It closes the
feedback loop on a provision run without manual intervention.

**Approach options:**
- A post-provision script that SSHes in and pulls the relevant log files (scp or ssh cat)
- Integrated into `reinstall_stack.py` as an optional `--fetch-logs` flag after
  `CREATE_COMPLETE`
- A standalone agentic playbook: "show me the provisioning logs for the current stack"

**Open design questions:**
- Which logs are most signal-dense? cloud-init-output.log covers UserData; minecraft-provision.log covers provision_servers.py; setup.sh output goes to cloud-init-output.log too since it runs inside UserData.
- Tail only (last N lines) or full retrieval?
- Failure detection: scan logs for known error patterns and surface a summary rather than raw output?
