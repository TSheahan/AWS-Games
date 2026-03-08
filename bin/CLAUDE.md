# bin/ — Developer Workstation Tools

Scripts in this directory run on the **developer workstation**, not on the EC2 instance.

---

## `reinstall_stack.py`

Manages the full CloudFormation stack lifecycle for rapid iteration.

**Runs on:** developer workstation
**AWS auth:** standard boto3 credential chain; `--profile` overrides the profile name
**Region:** `ap-southeast-4` (hard-coded constant at top of file)
**Template path:** `../cloudformation_server_stack.yaml` (relative to `bin/`)

Key behaviours:
- Errors if more than one `GameStack-*` stack exists simultaneously (manual cleanup required)
- Adopts `ExistingVolumeId` from the deleted stack's parameters automatically unless `--no-reuse-existing-volume` is passed
- Calls `ec2.describe_volumes` to detect the volume's Availability Zone and passes it as `AvailabilityZone` to CloudFormation — this prevents attachment failures when reusing an existing EBS volume
- Argument precedence: explicit CLI flag > environment variable > default value
- Safe by default: without `--execute` the script is a dry run — resolves all parameters and emits YAML to stdout but makes no AWS state changes
- `--execute` required to actually delete/create stacks; `--yes` / `-y` skips interactive confirmations when executing (for agentic or CI use)

Environment variables accepted:
- `GAME_PORT_START` → `--port-start`
- `GAME_PORT_END` → `--port-end`
- `GAME_SETUP_COMMAND` → `--setup-command` (required unless provided)
- `GAME_EXISTING_VOLUME_ID` → `--existing-volume-id`
- `GAME_INSTANCE_TYPE` → `--instance-type`

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
