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

## Persistent EIP + EBS stack

**Pain point:** The EIP and EBS volume are owned by the game stack. Every reinstall
destroys and recreates both — players lose their saved server address, and the volume
survives only via `DeletionPolicy: Retain` as a safety net, not intentional design.

**Designed approach:** A separate `GamePersistentStack` (fixed name) owns both
`AWS::EC2::EIP` and `AWS::EC2::Volume` with `DeletionPolicy: Retain`. The game stack
becomes fully stateless and ephemeral:

- `AWS::EC2::EIPAssociation` replaces `AWS::EC2::EIP` in the game stack
- `VolumeId` and `AllocationId` sourced via `Fn::ImportValue` from persistent stack exports
- `reinstall_stack.py` no longer needs to discover or pass these values — the template
  handles the dependency
- `NewVolume` / `CreateNewVolume` condition removed from game stack (hard cutover)

**Intentional hard CF dependency:** The game server stack importing from the persistent
stack via `Fn::ImportValue` means CloudFormation will block deletion of the persistent
stack while the game server stack exists. This is the desired protection — the persistent
stack cannot be accidentally torn down mid-session. The dependency releases when the game
server stack is deleted during a reinstall, at which point the persistent stack's exports
are free again for the new game server stack to import.

This is the architectural inverse of the control API pattern: the control API uses dynamic
lookup to *avoid* a hard dependency on the ephemeral game server stack. Here, a hard
dependency is exactly right because the persistent stack must outlive any number of game
server reinstalls.

**Resizing EBS:** Change `VolumeSize` parameter on the persistent stack → `update-stack` →
CloudFormation issues `ModifyVolume` online (no replacement, no detach).

**First-time setup** — `bin/setup_persistent_stack.py`:
- **Create mode** (no existing resources): standard `create_stack`
- **Import mode** (`--import-volume-id` / `--import-allocation-id`): adopts orphaned
  resources via `create-change-set --change-set-type IMPORT`; reads actual properties
  via `describe_volumes` / `describe_addresses` to match template parameters

Plan on file: `~/.claude/plans/lexical-cuddling-creek.md`

---

## `minecraft` wrapper — remaining live checks

Implemented and partially verified. Confirm on next session with a running server:

- Bash completion fires on a fresh boot (bash-completion package present, `/etc/bash_completion.d/` sourced)
- `minecraft status --yaml` output is well-formed
- `minecraft status <instance>` for a running server shows `🟢 running`
- `minecraft screen <instance>` attaches cleanly

---

## `minecraft autoshutdown` subcommand

**Background:** `minecraft-autoshutdown` (script + systemd timer, `--dry-run` supported)
is deployed and validated. The raw operations are still invoked directly via `systemctl`
and `journalctl`. Add a `minecraft autoshutdown` subcommand to the wrapper to eliminate
that.

**Subcommands:**
- `status` — timer enabled/active state, next trigger time, last run exit code; align
  visually with the emoji+table format of `minecraft status`
- `logs [N]` — last N *runs* from journald (`journalctl -u minecraft-autoshutdown.service`);
  default to enough to cover ~3 full runs; each run is delimited by the
  `=== minecraft-autoshutdown run at ... ===` header the script emits
- `run [--dry-run]` — manual one-shot trigger without affecting the timer schedule
  (`sudo /usr/local/bin/minecraft-autoshutdown`)
- `disable` / `enable` — toggle the timer
  (`systemctl disable/enable --now minecraft-autoshutdown.timer`)

**`minecraft status` integration:**
Add a summary row to the general status table so the autoshutdown mechanism stays visible
during routine health checks — maintaining user recall that the mechanism exists. One line
is enough: enabled/disabled state, next trigger time, and last run exit code. Example:

```
autoshutdown  enabled   next: 2026-03-08 14:00 AEDT  last: exit 0
```

This is intentionally lighter than `minecraft autoshutdown status` (the full deep-dive);
the goal is ambient awareness, not a duplicate view.

**Bash completion extension:**
Add `autoshutdown` (with its subcommands and `--dry-run`) to `minecraft-completion.bash`.

**Open design questions:**
- `minecraft autoshutdown status`: surface both last-trigger and next-trigger, or just
  next-trigger alongside exit code?
- `logs N`: run count confirmed as the right unit — requires parsing the delimiter header
  to slice correctly.

---

## `minecraft` wrapper — transparent operation mode

**Goal:** Surface the underlying commands and files the wrapper touches as it runs. A user
who runs `minecraft start vanilla` should be able to see that it ran
`systemctl start minecraft-vanilla.service` — and gradually build intuition for the raw
operations behind the convenience layer. This expresses a broader preference for tooling
that teaches rather than conceals.

**Approach:** A `--verbose` flag (or `MINECRAFT_VERBOSE=1` env var) that, before each
significant operation, prints the command about to be run. Consistent prefix keeps it
scannable and visually distinct from normal output:

```
[cmd] systemctl start minecraft-vanilla.service
[cmd] journalctl -u minecraft-autoshutdown.service -n 200
[file] /mnt/persist/minecraft/vanilla/console_2026-03-06_08-00-00.log
```

**Coverage across subcommands:**
- `start` / `stop` — the `systemctl` call for each unit
- `status` — `systemctl is-active` per unit; `systemctl list-unit-files` for discovery
- `screen` — the `screen -r` invocation and session name checked
- `reprovision` — the `python3 provision_servers.py` call with its flags
- `autoshutdown status` / `logs` / `run` — the underlying `systemctl` and `journalctl`
  calls; for `run`, also the log file and working directory resolved per server

**Scope question:** Apply only to the `minecraft` wrapper, or also instrument
`minecraft-autoshutdown` itself (e.g. emit `[file]` lines for the console log it reads)?
The autoshutdown script already logs its working directory and log file selections — a
`--verbose` pass-through from `minecraft autoshutdown run --verbose` could enable extra
detail there too.

**Bash completion:** `--verbose` should complete alongside existing flags.

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
