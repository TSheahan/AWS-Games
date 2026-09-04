# bin/ — Developer Workstation Tools

Scripts in this directory run on the **developer workstation**, not on the EC2 instance.

---

## `setup_persistent_stack.py`

One-time setup tool for the `GamePersistentStack` — the singleton stack that owns the EBS
volume and Elastic IP that survive game server reinstalls. Must be run before the first
`reinstall_stack.py` invocation.

**Runs on:** developer workstation
**AWS auth:** standard boto3 credential chain; `--profile` overrides
**Region:** `ap-southeast-4` (hard-coded constant at top of file)
**Template path:** `../persistent-resources.yaml` (relative to `bin/`)

Key behaviours:
- **Create mode** (no `--import-*` flags): `create_stack` with `--availability-zone` required
- **Import mode**: adopts orphaned EBS volume and/or EIP via CFN IMPORT changeset; derives
  actual resource properties from AWS (volume size/type/AZ; EIP public IP) to satisfy CFN
  import validation requirements
- Errors if the stack already exists (preventing double-creation)
- Safe by default: `--execute` required to write state; dry run emits YAML summary
- `--yes` / `-y` skips interactive confirmation when executing

Environment variables accepted:
- `GAME_PERSISTENT_STACK` → `--persistent-stack` (default: `GamePersistentStack`)

---

## `reinstall_stack.py`

Manages the ephemeral game server stack lifecycle for rapid iteration.

**Runs on:** developer workstation
**AWS auth:** standard boto3 credential chain; `--profile` overrides the profile name
**Region:** `ap-southeast-4` (hard-coded constant at top of file)
**Template path:** `../cloudformation_server_stack.yaml` (relative to `bin/`)

Key behaviours:
- Errors if more than one `GameStack-*` stack exists simultaneously (manual cleanup required)
- Reads `VolumeId` from `GamePersistentStack` outputs to derive `AvailabilityZone` via
  `ec2.describe_volumes`; errors clearly if the persistent stack is absent
- Passes `PersistentStackName` as a CFN parameter — the game stack template resolves
  `VolumeId` and `AllocationId` itself at deploy time via `Fn::ImportValue`; this creates
  a hard CFN dependency that blocks deletion of the persistent stack while the game stack exists
- Argument precedence: explicit CLI flag > environment variable > default value
- Safe by default: without `--execute` the script is a dry run — resolves all parameters and emits YAML to stdout but makes no AWS state changes
- `--execute` required to actually delete/create stacks; `--yes` / `-y` skips interactive confirmations when executing (for agentic or CI use)

Environment variables accepted:
- `GAME_PORT_START` → `--port-start`
- `GAME_PORT_END` → `--port-end`
- `GAME_SETUP_COMMAND` → `--setup-command` (required unless provided)
- `GAME_INSTANCE_TYPE` → `--instance-type`
- `GAME_PERSISTENT_STACK` → `--persistent-stack` (default: `GamePersistentStack`)

Stack names are timestamped: `GameStack-YYYYMMDD-HHMMSS`.

---

## `instance.py`

Resolves and acts on the current game server EC2 instance without storing a hardcoded instance
ID. The instance is looked up at call time from the active CloudFormation stack's resources, so
OS shortcuts pointing at this script survive stack reinstalls unchanged.

**Runs on:** developer workstation
**AWS auth:** standard boto3 credential chain; `--profile` overrides the profile name
**Region:** `ap-southeast-4` (hard-coded constant at top of file)

Subcommands:
- `start` — start the instance (`ec2.start_instances`)
- `stop` — stop the instance (`ec2.stop_instances`)
- `reboot` — reboot the instance (`ec2.reboot_instances`)
- `status` — print instance state, public IP (if present), and uptime (if running)
- `ssh` — open an SSH session via `os.execvp`, replacing this process cleanly

Stack discovery filters to `CREATE_COMPLETE` and `UPDATE_COMPLETE` only (operational stacks).
Errors cleanly if 0 or >1 active `GameStack-*` stacks are found.

**`--pause SECONDS`** — sleep after completion; intended for `.lnk` shortcuts where the terminal
window closes immediately on exit (e.g. `--pause 5`). Not applicable to `ssh`, which replaces
the process via `execvp`.

**SSH key path:** `~/.ssh/tim_ssh_to_game_server`
**SSH connects to:** `ServerIP` stack output (the Elastic IP), not the instance's current public
IP — stable across stop/start cycles.

---

## `deploy_control_api.py`

Deploys or updates the `GameControlApi` CloudFormation stack, which exposes a Lambda Function
URL for game server start/stop from mobile home screen shortcuts.

**Runs on:** developer workstation
**AWS auth:** standard boto3 credential chain; `--profile` overrides the profile name
**Template:** `../cloudformation_control_api_stack.yaml` (relative to `bin/`)
**Stack name:** `GameControlApi` — fixed, not timestamped; updated in place across redeploys

This stack is independent of and outlives the game server stack. Do not delete it during game
server reinstalls.

Key behaviours:
- Detects existing stack → `update_stack` if present, `create_stack` if not
- `update_stack` handles the "No updates are to be performed" no-op case cleanly
- Safe by default: `--execute` required to write state; dry run reports `operation` and `stack_name`
- `--api-key` accepts the key on the CLI; omit it to be prompted via `getpass` (input hidden)
- The API key is **never logged** — omitted from all parameter summary output
- On success: prints YAML with stack outputs and logs the two shortcut URLs (with `<your-key>` placeholder)

**API key scope:** game instance start/stop only — not a privileged credential. Revoke by
changing the `ApiKey` CF parameter and redeploying.

---

## Python conventions

- Dependencies: `boto3`, `pyyaml`, `botocore` (see `requirements.txt` in repo root)
- No custom session management beyond `boto3.Session(profile_name=...)` — use standard credential chain
- `ClientError` and `WaiterError` from `botocore.exceptions` are the primary exception types handled
