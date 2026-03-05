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

## Systemd service status shortcut

**Pain point:** No ergonomic shortcut to surface the health of all `minecraft-*` systemd
units at a glance from the server prompt.

**Current state — `ec2/minecraft/mcstatus.sh` reviewed (2026-03-05):**

Script iterates `systemctl list-unit-files --no-legend 'minecraft-*.service'` and calls
`systemctl is-active` per unit. Has a bug: `is-active` exits non-zero for inactive services,
so `|| echo "unknown"` fires — both the real status text and "unknown" are captured in the
same command substitution, producing malformed output (`❓ inactive` line + a dangling
`unknown` line per unit). Fix: replace `|| echo "unknown"` with `|| true` and a separate
empty-check.

Observed on first boot (all servers inactive as expected — `vanilla` configured to
auto-start on next boot, not this one):

```
minecraft-famine.service     ❓ inactive    ← should be ⚪ stopped
minecraft-vanilla.service    ❓ inactive    ← ditto
unknown                                     ← spurious, one per unit
```

**Next steps:**
1. Fix the `|| echo "unknown"` bug so inactive services render as `⚪ stopped`.
2. Verify the `🟢 running` path against a live `vanilla` instance after next boot.
3. Streamline invocation — currently `bash mcstatus.sh` from the script directory; should
   be callable from anywhere (symlink into `PATH`, or install to `/usr/local/bin/`).
4. Profile for agentic compatibility — consider a `--yaml` flag for machine-readable output
   so an agent can parse fleet state without screen-scraping emoji columns.

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
