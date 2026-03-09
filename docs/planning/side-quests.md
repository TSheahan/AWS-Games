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

## Persistent EIP + EBS stack — COMPLETE (2026-03-08)

Implemented. `persistent-resources.yaml` + `bin/setup_persistent_stack.py` added;
`cloudformation_server_stack.yaml` and `bin/reinstall_stack.py` updated.
Game stack is now fully stateless. See CHANGELOG.md.

---

## `minecraft` wrapper — remaining live checks — COMPLETE (2026-03-08)

All checks confirmed on live server:

- ~~Bash completion fires on a fresh boot (bash-completion package present, `/etc/bash_completion.d/` sourced)~~ — confirmed 2026-03-08
- ~~`minecraft status --yaml` output is well-formed~~ — confirmed 2026-03-08
- ~~`minecraft status <instance>` for a running server shows `🟢 running`~~ — confirmed 2026-03-08
- ~~`minecraft screen <instance>` attaches cleanly~~ — confirmed 2026-03-08

---

## `minecraft autoshutdown` subcommand — COMPLETE (2026-03-09)

Implemented. `minecraft autoshutdown status|logs [N]|run [--dry-run]|enable|disable`
added to the wrapper; `minecraft status` now appends an autoshutdown summary row (and
`autoshutdown:` block in `--yaml` mode) when showing all instances. Bash completion
extended with subcommands and `--dry-run`; missing `grep -v` filter for
`minecraft-autoshutdown.service` in the completion's instance query also fixed. See
CHANGELOG.md.

**TODO (low priority) — per-server idle state in `minecraft status`:**
Surface idle detection state alongside running/stopped status — i.e. whether each server
is currently considered idle by the autoshutdown logic (no players, pause signal seen in
console log tail). Reuse the detection logic from `minecraft-autoshutdown` rather than
re-implementing it.

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

---

## Composable setup command

**Pain point:** `--setup-command` is a single opaque shell string the caller must assemble
by hand every deploy, packing four concerns into one value: script path, server version,
JAR URL, and Java package.

**Observation:** Script path and Java package are effectively static (change with a new game
type or a major Java bump — years apart). Server version is sticky across deploys. JAR URL
is per-version but tightly coupled to server version. The full string is redundant most of
the time.

**Decomposition:** `reinstall_stack.py` gains first-class arguments (`--game-type`,
`--server-version`, `--jar-url`, `--java-package`) with defaults where natural. It composes
them into the `SetupCommand` string internally. Raw `--setup-command` stays as an escape
hatch, mutually exclusive with the composed form.

**Server setup as a post-instantiation concern:** JAR URL and server version are the volatile
components, and they're purely server-setup concerns — they don't affect infrastructure
provisioning. Moving server setup out of UserData into a post-boot action (e.g.
`instance.py setup`) would:
- Let the instance stand up faster (UserData stops at volume mount + git clone)
- Make setup retryable without stack recreation (setup.sh is already idempotent)
- Give real-time visible output instead of buried cloud-init logs
- Naturally house the version/URL arguments where they belong — on the server setup
  command, not the infrastructure provisioning command

Zero-touch first deploys can be preserved: UserData invokes setup by default, `instance.py
setup` is the re-run / iterate path. The two improvements (composable arguments, post-boot
setup path) are orthogonal.

**Open questions:**
- Should composed arguments also persist in a config file to reduce repetition further?
- `instance.py setup` scope: full setup.sh, or targetable sub-steps (e.g. re-provision only)?
