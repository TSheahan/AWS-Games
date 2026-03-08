# Changelog

Notable milestones and changes to AWS-Games, grouped by session. Individual commit messages
provide finer-grained detail; this log captures intent and trajectory.

---

## 2026-03-08 — Persistent EIP + EBS stack

**GamePersistentStack — EIP and EBS volume moved out of the ephemeral game stack**

The EIP and EBS volume were previously owned by the timestamped `GameStack-*`, which meant
every reinstall destroyed and recreated the EIP (breaking saved server addresses) and left the
volume dangling with only `DeletionPolicy: Retain` as a safety net.

New architecture:
- `persistent-resources.yaml` — new singleton CFN template; creates `PersistentEIP` and
  `PersistentVolume`, both with `DeletionPolicy: Retain`; exports `VolumeId`, `AllocationId`,
  `PublicIp` as named CFN stack exports
- `bin/setup_persistent_stack.py` — one-time setup tool; supports create mode
  (fresh resources) and import mode (`--import-volume-id` / `--import-allocation-id`)
  for adopting existing orphaned resources via CFN IMPORT changeset; three-step mixed
  import flow handles the CFN restriction that a new stack's IMPORT changeset must list
  ALL resources: (1) `create_stack` with fresh resources only, (2) IMPORT changeset with
  all resources but only pre-existing outputs (CFN also forbids adding outputs in an IMPORT
  changeset), (3) regular `update_stack` to add outputs for the newly imported resources
- `cloudformation_server_stack.yaml` — `NewVolume`, `CreateNewVolume` condition,
  `HasAvailabilityZone` condition, and `ExistingVolumeId` parameter removed; `ServerEIP`
  replaced by `EIPAssociation`; `PersistentStackName` parameter added (default:
  `GamePersistentStack`); `VolumeId` and `AllocationId` resolved at deploy time via
  `Fn::ImportValue`; `ServerIP` output changed from `!GetAtt EIPAssociation.PublicIp`
  (invalid — `AWS::EC2::EIPAssociation` has no `PublicIp` attribute) to
  `Fn::ImportValue: !Sub "${PersistentStackName}-PublicIp"`; `Metadata` block added
  documenting the update-immutability constraint on `AWS::EC2::VolumeAttachment` and
  `AWS::EC2::EIPAssociation` (no read handlers; re-processed on any update; recreate-only
  is the correct pattern) with a TODO for future mitigation analysis
- `bin/reinstall_stack.py` — volume-reuse logic removed; reads `VolumeId` from persistent
  stack outputs to derive `AvailabilityZone`; passes `PersistentStackName` as a CFN
  parameter so the game stack can resolve `Fn::ImportValue` references itself; `--delete-only`
  flag added for teardown without redeploy; `UPDATE_ROLLBACK_COMPLETE` added to the stack
  status filter so stacks stuck in that state are visible and deletable

The game stack is now fully stateless. Reinstalls do not affect the EIP or volume.
The `Fn::ImportValue` references create a hard CFN dependency: CloudFormation blocks
deletion of the persistent stack while the game stack exists. The dependency releases
when the game stack is deleted during a reinstall, freeing the exports for the next
game stack to import.

End-to-end verified: `GamePersistentStack` created by importing the orphaned volume
(`vol-0f4cee5cb4bc42932`, world data intact, 2024 creation date) and allocating a fresh
EIP; game stack rebuilt cleanly; NVMe serial-based volume detection succeeded on second
poll (5 s); all Minecraft servers provisioned; `balkan` confirmed running post-reboot.

CLAUDE.md hierarchy updated throughout to reflect the new two-stack architecture.

---

## 2026-03-08 — CLAUDE.md hierarchy maintenance and strategic planning update

**Operational knowledge split — `memory/operations.md` → CLAUDE.md hierarchy**

The strategic direction had flagged this as a prerequisite for agentic operational patterns:
private memory was mixing personal workstation state with system procedures, making playbooks
fragile (dependent on private memory being loaded). Split completed:

- `memory/operations.md` trimmed to personal state only: active volume ID, ports, JAR URL,
  redeployment runbook with real values. All procedure content removed.
- Root `CLAUDE.md` updated: fixed stale screen session names (was generic `screen -S minecraft`;
  corrected to per-server `minecraft-<server_id>`); File Map brought current — removed deleted
  `mcstatus.sh`, added `cloudformation_control_api_stack.yaml`, `bin/instance.py`,
  `bin/deploy_control_api.py`, `ec2/minecraft/minecraft`, `minecraft-completion.bash`,
  `minecraft-autoshutdown`; added log locations, port range constraint, EBS mount prerequisite,
  SSH access, and first-deploy steps checklist to Operational Notes.
- `ec2/minecraft/CLAUDE.md` updated: added JVM flags to generated start script documentation,
  added `journalctl` commands to the systemd integration section, added design principle
  section (provision_servers.py owns all server-specific setup).

**`docs/planning/strategic-direction.md` update**

- Marked the operational knowledge split as resolved with outcome documented
- Added `bin/instance.py` and mobile control API as Priority 1 deliveries (previously
  undocumented in the planning file)
- Removed the resolved open design question and prerequisite
- Focused "what remains" on named playbooks and precondition checks; provisioning log
  retrieval named as the natural first playbook

---

## 2026-03-08 — Mobile control API and workstation instance control

**Mobile start/stop control API (`cloudformation_control_api_stack.yaml` + `bin/deploy_control_api.py`)**

- Added `cloudformation_control_api_stack.yaml` — a stable, independently deployed CloudFormation
  stack exposing a Lambda Function URL for starting and stopping the game server EC2 instance from
  Android home screen shortcuts (capability URL pattern: the bookmark IS the credential)
- Lambda function (`GameControlApi`, python3.12) performs dynamic GameStack lookup at call time —
  no hardcoded instance ID; resolves active stack → `ServerInstance` resource → instance ID
- API key validated from `?key=<value>` query string before any AWS call; scoped to EC2
  start/stop only; `NoEcho: true` parameter prevents key appearing in CF console or logs
- Deployed to ap-southeast-2 (Sydney) — `AWS::Lambda::Url` is unavailable in ap-southeast-4
  (Melbourne) at both CF template and API levels as of 2026-03; Lambda's EC2/CF clients target
  Melbourne explicitly via `region_name="ap-southeast-4"` — cross-region boto3 calls are standard
- Root cause of all 403 Forbidden failures traced to an October 2025 AWS breaking change: new
  Function URLs require both `lambda:InvokeFunctionUrl` AND `lambda:InvokeFunction` in the
  resource-based policy; both permissions present as `ControlApiFunctionUrlPermission` and
  `ControlApiInvokeFunctionPermission`
- `Metadata` block documents RegionalWorkaround (Sydney rationale, cross-region mechanics,
  migration path back to Melbourne, two-permission requirement history) and SecurityModel
  (capability URL pattern rationale, threat model, API Gateway comparison, residual risks)
- Added `bin/deploy_control_api.py` — create-or-update deployer; `--execute` required for state
  changes (safe by default); API key via `--api-key` or `getpass` prompt; key never logged;
  prints shortcut URLs on success
- API Gateway HTTP API fallback plan frozen in private memory (`api-gateway-plan.md`) — do not
  implement unless Lambda Function URL definitively fails

**Workstation instance control wrapper (`bin/instance.py`)**

- Added `bin/instance.py` — resolves the current `GameStack-*` EC2 instance ID from
  CloudFormation at call time; OS shortcuts pointing at this script survive stack reinstalls
  without modification (no stored instance ID)
- Subcommands: `start`, `stop`, `reboot`, `status` (state / public IP / uptime), `ssh`
- `ssh` uses `os.execvp` to replace the Python process cleanly — no subprocess wrapper
- `ssh` connects to the `ServerIP` stack output (Elastic IP) rather than the transient public
  IP — stable across stop/start cycles
- `--pause SECONDS` flag sleeps after completion for `.lnk` shortcut invocations where the
  terminal closes immediately on exit; not applicable to `ssh` (process replaced by execvp)
- `--profile` flag consistent with existing `bin/` tooling

---

## 2026-03-08 — `minecraft-autoshutdown` idle-shutdown service

- Added `ec2/minecraft/minecraft-autoshutdown` — shuts down the EC2 instance when all
  provisioned Minecraft servers have been idle for at least one 30-minute check cycle
- Idle detection reads the tail of each server's console log: a `"Server empty for 60
  seconds, pausing"` signal with no subsequent `"joined the game"` entry → IDLE; absent
  pause signal or join after pause → NOT IDLE. All servers must be idle to trigger shutdown.
- Global uptime guard: skips idle checks for the first 3 hours after boot to prevent
  premature shutdown during startup and provisioning
- Added `minecraft-autoshutdown.service` (oneshot) and `minecraft-autoshutdown.timer`
  (30-minute schedule, `OnBootSec=30min`); `setup.sh` installs all three and enables the
  timer at provision time
- Added `--dry-run` flag: runs full detection logic and logs per-server verdicts without
  executing `shutdown -h now`; uptime guard is bypassed in dry-run mode so the full check
  can be exercised on a fresh instance
- Bug fix: `_list_instances()` in both `minecraft-autoshutdown` and the `minecraft` wrapper
  excluded `minecraft-autoshutdown.service` from the `minecraft-*.service` glob — without
  this, the service would classify itself as NOT IDLE (active, no WorkingDirectory) and
  block every timer-triggered shutdown
- Validated on live instance: all three detection paths confirmed (`IDLE`, `NOT IDLE —
  player joined`, `NOT IDLE — no pause signal in tail`)

---

## 2026-03-05 — `minecraft` admin wrapper command

- Added `ec2/minecraft/minecraft` — a `minecraft <subcommand>` wrapper for all common EC2 admin tasks, installed to `/home/ec2-user/bin/` by `setup.sh`
- Subcommands: `status [instance] [--yaml]`, `start [instance]`, `stop [instance]`, `screen <instance>`, `reprovision`
- `--yaml` flag on `status` emits machine-readable YAML for agent consumption
- Added `ec2/minecraft/minecraft-completion.bash` — bash completion drop-in installed to `/etc/bash_completion.d/`; completes subcommands and instance names; suppresses `--yaml` once already present
- Deleted `mcstatus.sh` — functionality fully absorbed into `minecraft status`; bug fixed in the process (`|| echo "unknown"` → `|| true` to prevent spurious output from `is-active` non-zero exit)
- `setup.sh` installs both files at provision time; `mkdir -p /home/ec2-user/bin` ensures the directory exists on fresh instances
- Verified on live instance: `minecraft status` (emoji table), `minecraft reprovision`

---

## 2026-03-05 — Bug fix: eula.txt and server.properties ownership

- `guarded_write_text` now accepts an `owner` parameter; when set, calls `chown <owner>:<owner>` after writing
- `update_server_properties` and `ensure_eula_accepted` pass `owner=EC2_USER` so both files are written as `ec2-user:ec2-user` rather than `root:root`
- Systemd unit files continue to be written without `owner`, preserving root ownership

---

## 2026-03-04 — Repo restructure, provisioning hardening, agentic readiness, and end-to-end validation

**Repo restructure**

- Moved all EC2-side scripts under `ec2/`, separating them cleanly from workstation tooling
  in `bin/`; `setup.sh` and `update-release.sh` relocated from repo root
- Documented the game extensibility model: `ec2/<game>/` as the dispatch mechanism for
  game-specific setup; a shared orchestration layer deliberately deferred until a second
  game type materialises

**Setup and provisioning refactor**

- Refactored `setup.sh`: removed `--server-folder` parameter; JAR placed at
  `/mnt/persist/minecraft/server_<version>.jar`; all server-specific setup delegated to
  `provision_servers.py --update --provision`
- Deleted `start-minecraft.sh` and `stop-minecraft.sh`; `provision_servers.py` generates
  per-server equivalents from config and is now the authoritative source of server
  configuration
- Aligned `TimeoutStopSec=90` throughout

**`reinstall_stack.py` safety and agentic readiness**

- Added `--execute` flag; without it the script is a dry run — safe by default
- Added `--yes` / `-y` to skip interactive confirmation prompts for non-interactive and
  agentic use
- Status and progress output routed to stderr via logger; structured output (YAML parameter
  summary) emitted to stdout — clean pipe-friendliness
- Fixed `GAME_EXISTING_VOLUME_ID` env var silently not wiring through to
  `--existing-volume-id` default

**AMI resolution**

- Replaced hard-coded AMI ID with an SSM dynamic reference in the CloudFormation template
  (`{{resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.12-arm64}}`);
  each deploy resolves to the current security-patched build — no manual AMI ID maintenance required

**Provisioning hardening (driven by a live end-to-end provision run)**

- Replaced heuristic EBS device detection (size/type matching) with NVMe serial number
  matching against the known volume ID, with a root-exclusion fallback
- Fixed new-volume path: `reinstall_stack.py` now always resolves and passes the AZ
  explicitly, eliminating a potential CloudFormation dependency loop on `NewVolume`
- Quieted `yum` and `wget` output (`-q`) to reduce UserData log noise

---

## 2026-03-03 — CLAUDE.md context architecture and documentation enrichment

- Created `CLAUDE.md` hierarchy at repo root, `bin/`, and `ec2/minecraft/` — project
  context, conventions, and architecture auto-loaded into every Claude Code session
- Enriched `README.md` with integrated automation narrative, provisioning chain diagram,
  design decisions table, and per-component breakdown

---

## 2026-01-19 — Server status tooling

- Added `mcstatus.sh`: one-shot status display of all `minecraft-*.service` systemd units

---

## 2026-01-17 — Multi-server provisioning engine

- Introduced `provision_servers.py`: reads `minecraft-servers.yaml` from a private config
  repo and generates per-server systemd units, start/stop scripts, and `server.properties`
- Mode separation: `--update` (pull config), `--read-only` (validate only), `--provision`
  (full write); independently composable
- Port configuration sourced from `/home/ec2-user/game-ports.json` written by UserData
- Log written to `/var/log/minecraft-provision.log` (world-readable)
- Per-server enabled/disabled state tracking

---

## 2026-01-07 — EBS mount reliability

- Switched fstab entries to UUID-based identification, defending against block device order
  variation across instance reboots

---

## 2026-01-04 — First full dev release

Consolidated a working Minecraft server stack into a repeatable, parameterised deployment.

- CloudFormation template parameterised: `InstanceType` (default `t4g.medium`), game port
  range, Java package, setup command
- ARM64 / Graviton instance target established throughout
- `reinstall_stack.py`: full stack lifecycle tool — delete existing stack, create timestamped
  replacement (`GameStack-YYYYMMDD-HHMMSS`), wait for `CREATE_COMPLETE`, print outputs
- EBS volume provisioned with `DeletionPolicy: Retain`; world data survives stack deletion
- EBS reuse: `reinstall_stack.py` detects an existing volume and passes it through on
  redeploy; AZ alignment enforced to prevent attachment failures
- UserData waits up to 120 s for the EBS device to appear before formatting/mounting —
  handles the lag between CloudFormation attachment completion and kernel visibility
- NVMe device naming handled correctly in mount logic
- Open port range written to `/home/ec2-user/game-ports.json` for downstream scripts
- `update-release.sh`: convenience script for AL2023 release version upgrades
- Start/stop scripts updated: contemporary logging flag; IPv4 bind flag to prevent IPv6
  preemption on the listen port
- EULA check in server start script
- `requirements.txt` added (`boto3`, `pyyaml`, `botocore`)
- `README.md` added

---

## 2024-07-01 — Java 21 and Amazon Linux 2023 migration

- Upgraded from Amazon Linux 2 to Amazon Linux 2023
- Adopted Java 21 (Amazon Corretto); `java-package` parameter added to `setup.sh` to make
  JDK version configurable

---

## 2024-04-01 — Project inception

- CloudFormation template: EC2 instance, EBS volume, Elastic IP, Security Group
- UserData bootstrap: clone this repository onto the EC2 instance, execute a configurable
  `SetupCommand` parameter
- Initial `setup.sh`: installs JDK, downloads Minecraft server JAR
