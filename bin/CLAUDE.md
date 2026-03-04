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
- Always shows a full parameter summary and requires Enter confirmation before creating

Environment variables accepted:
- `GAME_PORT_START` → `--port-start`
- `GAME_PORT_END` → `--port-end`
- `GAME_SETUP_COMMAND` → `--setup-command` (required unless provided)
- `GAME_EXISTING_VOLUME_ID` → `--existing-volume-id`
- `GAME_INSTANCE_TYPE` → `--instance-type`

Stack names are timestamped: `GameStack-YYYYMMDD-HHMMSS`.

---

## Python conventions

- Dependencies: `boto3`, `pyyaml`, `botocore` (see `requirements.txt` in repo root)
- No custom session management beyond `boto3.Session(profile_name=...)` — use standard credential chain
- `ClientError` and `WaiterError` from `botocore.exceptions` are the primary exception types handled
