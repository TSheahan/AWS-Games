# AWS-Games — Project Guide for Claude Code

## Project Purpose

Developer tooling and infrastructure-as-code for hosting game servers (primarily Minecraft) on AWS EC2. The project owns:
- A **CloudFormation template** that provisions a complete game server stack
- **Developer tools** (`bin/`) that run on the workstation to manage deployments
- **Server-side scripts** that run on the EC2 instance during and after provisioning

---

## Architecture Overview

```
Developer workstation
  └── bin/reinstall_stack.py          # Drives CloudFormation deploy/redeploy
        └── cloudformation_server_stack.yaml
              └── EC2 UserData bootstrap
                    ├── Mount EBS volume → /mnt/persist
                    ├── git clone AWS-Games repo
                    └── Run SetupCommand (e.g. ec2/minecraft/setup.sh)
                          └── Install JDK, download server JAR
                                └── provision_servers.py
                                      └── git clone AWS-Games-Config repo
                                            └── minecraft-servers.yaml → per-server systemd units, start/stop scripts
```

### AWS Resources (per CloudFormation stack)
| Resource | Type | Notes |
|---|---|---|
| `ServerInstance` | EC2 | Amazon Linux 2023, ARM64/Graviton, t4g.medium default |
| `ServerEIP` | Elastic IP | Static public IP, output as `ServerIP` |
| `NewVolume` | EBS gp3 10 GB | **DeletionPolicy: Retain** — data survives stack deletion |
| `PersistentVolumeAttachment` | Volume Attachment | Attached at `/dev/sdf`, mounted at `/mnt/persist` |
| `ServerSecurityGroup` | Security Group | SSH (22) + configurable game port range |

**AMI:** Resolved dynamically at deploy time via SSM — `{{resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.12-arm64}}` (AL2023 kernel-6.12 ARM64, EoL June 2029)
**Key Pair:** `tim_ssh_to_game_server` (must exist in region before deploy)
**Region:** `ap-southeast-4` (Melbourne, Australia) — hard-coded throughout

---

## File Map

| Path | Runs on | Purpose |
|---|---|---|
| `cloudformation_server_stack.yaml` | AWS | Main game server IaC template; creates EC2, EBS, EIP, SG |
| `cloudformation_control_api_stack.yaml` | AWS | Lambda Function URL stack for mobile start/stop control |
| `bin/reinstall_stack.py` | Workstation | Delete old stack, create new timestamped stack |
| `bin/instance.py` | Workstation | Resolve and control the game server EC2 instance (start/stop/reboot/status/ssh) |
| `bin/deploy_control_api.py` | Workstation | Deploy or update the mobile control API CloudFormation stack |
| `ec2/minecraft/setup.sh` | EC2 instance (root, via UserData) | Instance-level setup: Java install, JAR download, invokes provision_servers.py |
| `ec2/minecraft/provision_servers.py` | EC2 instance (root) | Multi-server systemd unit management |
| `ec2/minecraft/minecraft` | EC2 instance | Admin wrapper; installed to `/home/ec2-user/bin/minecraft` |
| `ec2/minecraft/minecraft-completion.bash` | EC2 instance | Bash completion drop-in; installed to `/etc/bash_completion.d/` |
| `ec2/minecraft/minecraft-autoshutdown` | EC2 instance | Idle-detection shutdown script (oneshot service + 30-min timer) |
| `ec2/update-release.sh` | EC2 instance (ec2-user) | AL2023 release version upgrade helper |
| `requirements.txt` | Workstation | boto3, pyyaml, botocore |

---

## Deployment Workflow

### Developer side (`bin/reinstall_stack.py`)
1. Discovers existing `GameStack-*` stacks; errors on multiples
2. Prompts to delete existing stack; waits for `DELETE_COMPLETE`
3. Reads `ExistingVolumeId` from old stack parameters (for data reuse)
4. Calls `ec2.describe_volumes` to detect the volume's AZ → passes as `AvailabilityZone` param
5. Prompts to confirm creation with full parameter summary
6. Reads `cloudformation_server_stack.yaml` from `../` (relative to `bin/`)
7. Creates `GameStack-YYYYMMDD-HHMMSS` stack; waits for `CREATE_COMPLETE`
8. Prints all stack outputs including `ServerIP`

**Env var overrides:** `GAME_PORT_START`, `GAME_PORT_END`, `GAME_SETUP_COMMAND`, `GAME_EXISTING_VOLUME_ID`, `GAME_INSTANCE_TYPE`

### EC2 UserData bootstrap (in the CloudFormation template)
1. Sets timezone: `Australia/Melbourne`
2. Waits up to 120s for `/dev/sdf` to appear (EBS attachment can lag)
3. Formats EBS as ext4 if new; mounts to `/mnt/persist`; adds UUID fstab entry
4. Clones this repository (`AWS-Games`) into ec2-user's home
5. Writes `/home/ec2-user/game-ports.json` with port range
6. Copies `ec2/update-release.sh` to ec2-user home
7. Executes the `SetupCommand` parameter (e.g. `./ec2/minecraft/setup.sh --server-version=1_21_11 ...`)

### setup.sh (instance-level first-boot setup)
- Must be run from repo root; validates `/mnt/persist` is mounted
- Installs JDK via yum (e.g. `java-21-amazon-corretto-devel`)
- Downloads server JAR → `/mnt/persist/minecraft/server_<version>.jar` (skipped if already present)
- Invokes `provision_servers.py --update --provision` for all server-specific setup

---

## Multi-Server Provisioning (`ec2/minecraft/provision_servers.py`)

Reads `minecraft-servers.yaml` from a **separate private config repo** (`AWS-Games-Config`). Generates per-server systemd units, start/stop scripts, and `server.properties`.

**Modes:**
- `--update` — pull latest config from remote repo
- `--read-only` — validate without writing
- `--provision` — full write (requires root)

**Config repo:** `https://github.com/TSheahan/AWS-Games-Config.git`
**Port source:** `/home/ec2-user/game-ports.json` (written during UserData)
**Log:** `/var/log/minecraft-provision.log` (world-readable)

Expected YAML structure:
```yaml
servers:
  server_id:
    folder: directory_name
    port: 25565
    start_command: "java -Xmx4G -jar minecraft_server.jar nogui"
    friendly_name: "Optional Display Name"
    start_on_boot: true
provisioned:
  - server_id
```

---

## Operational Notes

- **EULA:** Must be accepted manually after first deploy — SSH in and set `eula=true` in `eula.txt` in each server's working directory under `/mnt/persist/minecraft/`
- **Volume retention:** EBS has `DeletionPolicy: Retain`; always check for orphan volumes after teardown
- **AZ pinning:** `reinstall_stack.py` auto-detects volume AZ to prevent attachment failures on reuse
- **Screen sessions:** Per-server sessions named `minecraft-<server_id>`; use `minecraft screen <instance>` (preferred) or `screen -r minecraft-<server_id>` (direct); detach with Ctrl+A, D
- **Java version:** Java 21 (Corretto) for Minecraft 1.21.x; comment in setup.sh notes 2026 versioning may require Java 25
- **Root required:** `provision_servers.py` and `setup.sh` must run as root on the instance; both abort if `/mnt/persist` is not mounted
- **Single stack constraint:** Script errors if multiple `GameStack-*` stacks exist simultaneously
- **Port range constraint:** game port numbers in `minecraft-servers.yaml` must fall within the `GAME_PORT_START`–`GAME_PORT_END` CloudFormation range
- **Log locations:** UserData → `/var/log/cloud-init-output.log`; provisioning → `/var/log/minecraft-provision.log` (world-readable); server console → `/mnt/persist/minecraft/<folder>/console_YYYY-MM-DD_HH-MM-SS.log`
- **SSH access:** `python bin/instance.py ssh` (preferred; resolves current IP automatically); fallback: `ssh -i ~/.ssh/tim_ssh_to_game_server ec2-user@<ServerIP>`

### First-deploy steps

Before running `reinstall_stack.py`:
- Confirm `tim_ssh_to_game_server` key pair exists in `ap-southeast-4`
- Prepare `GAME_SETUP_COMMAND`; for volume reuse, set `GAME_EXISTING_VOLUME_ID` or pass `--existing-volume-id`

After `CREATE_COMPLETE`:
1. SSH in: `python bin/instance.py ssh`
2. Accept EULA: set `eula=true` in `eula.txt` in each server folder under `/mnt/persist/minecraft/`
3. Start servers: `minecraft start <server_id>`

---

## Development Conventions

- Python tools in `bin/` use standard boto3 credential chain; `--profile` flag overrides
- CloudFormation stack names are timestamped: `GameStack-YYYYMMDD-HHMMSS`
- Template path is relative: `../cloudformation_server_stack.yaml` from `bin/`
- Shell scripts use bash and target Amazon Linux 2023 (AL2023)
- Graviton (ARM64) instances only — scripts and AMIs are ARM-specific

### Game extensibility model

`ec2/` is game-agnostic. Each game type owns a subdirectory (`ec2/minecraft/`, `ec2/<game>/`, …). The CloudFormation `SetupCommand` parameter carries the path to the relevant game's setup script — the path *is* the dispatch mechanism.

A game-agnostic `ec2/setup.sh` orchestrator layer has been considered and deliberately deferred. Introduce it when a second game type is added and shared instance-level setup work is real rather than projected. Do not introduce it speculatively.
