# AWS-Games

An integrated automation for provisioning persistent game servers on AWS — from a single command on the developer workstation to a live, self-configured Minecraft server running on EC2.

The system is self-bootstrapping: the CloudFormation stack it deploys causes the EC2 instance to clone *this repository* at launch and run its own setup scripts. Infrastructure and application provisioning are unified in one coherent workflow.

---

## What it does

Running one command on the developer workstation:

```bash
python bin/reinstall_stack.py --setup-command "./setup.sh ..."
```

...triggers a fully automated sequence:

1. Any existing stack is cleanly torn down (with data retained on the EBS volume)
2. A new CloudFormation stack provisions EC2, EBS, Elastic IP, and Security Group
3. The EC2 instance mounts persistent storage and clones this repository
4. The instance executes `ec2/minecraft/setup.sh` to install Java, download the server JAR, and register a systemd service
5. `provision_servers.py` (optionally) reads a separate config repo to generate per-server systemd units

The result is a running game server, reconstructed from scratch, with world data intact from the previous deployment.

---

## Provisioning chain

```
Developer workstation
└── bin/reinstall_stack.py
      ├── Discovers and deletes existing GameStack-* stack
      ├── Auto-detects EBS volume Availability Zone (for safe reattachment)
      └── Creates GameStack-YYYYMMDD-HHMMSS via CloudFormation
            └── EC2 UserData bootstrap (runs as root at launch)
                  ├── Waits for EBS device, formats if new, mounts to /mnt/persist
                  ├── git clone github.com/TSheahan/AWS-Games  ← repo clones itself onto the instance
                  ├── Writes game-ports.json with port range
                  └── Executes SetupCommand parameter (e.g. ./ec2/minecraft/setup.sh ...)
                        └── ec2/minecraft/setup.sh
                              ├── Installs Java (Amazon Corretto via yum)
                              ├── Downloads server JAR, creates symlink
                              ├── Copies start/stop wrapper scripts
                              └── Creates and enables minecraft-server.service
                                    └── (optional) ec2/minecraft/provision_servers.py
                                          ├── Clones AWS-Games-Config repo
                                          ├── Reads minecraft-servers.yaml
                                          └── Generates per-server systemd units
```

---

## AWS resources created (per stack)

| Resource | Type | Notes |
|---|---|---|
| `ServerInstance` | EC2 | Amazon Linux 2023, ARM64 (Graviton), configurable instance type |
| `ServerEIP` | Elastic IP | Static public address; output as `ServerIP` |
| `NewVolume` | EBS gp3 10 GB | **DeletionPolicy: Retain** — survives stack deletion |
| `PersistentVolumeAttachment` | Volume Attachment | `/dev/sdf` → `/mnt/persist` |
| `ServerSecurityGroup` | Security Group | SSH (22) + configurable game port range |

---

## Design decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Persistent storage** | EBS with `DeletionPolicy: Retain` | World data survives full stack teardown; the same volume is reused on next deploy without manual backup/restore |
| **AZ pinning** | Auto-detect volume AZ via `describe_volumes` | EBS volumes are AZ-scoped; a mismatched AZ at launch causes attachment failure. Detection is automatic and transparent in `reinstall_stack.py`. |
| **Instance architecture** | Graviton ARM64 (t4g family) | ~20% cost reduction over equivalent x86; Corretto Java performs well on ARM for JVM workloads |
| **Config repo separation** | `minecraft-servers.yaml` in a separate repo | Decouples server configuration changes from infrastructure code changes; different deploy cadence for each |
| **Timestamped stack names** | `GameStack-YYYYMMDD-HHMMSS` | Collision-free rapid iteration; no manual name management during development |
| **Self-cloning bootstrap** | UserData clones this repo at launch | Instance always runs the code version matched to the deployed template; no AMI baking required |
| **systemd + screen** | systemd manages lifecycle; screen provides console | systemd handles auto-start and graceful shutdown signalling; screen survives SSH disconnect |
| **Config-driven multi-server** | `provision_servers.py` reads YAML | Adding a server requires only a config change, not a code or infrastructure change |

---

## Components

### `cloudformation_server_stack.yaml`
The infrastructure template. Parameterised for port range, instance type, setup command, and optional volume reuse. The UserData section bridges infrastructure and application: it mounts storage, clones the repo, and hands off to application-layer scripts.

### `bin/reinstall_stack.py`
Developer workflow tool. Handles the full stack lifecycle: discovery, safe deletion, volume ID adoption, AZ detection, and timestamped creation. Supports environment variable configuration for scripted invocation. Argument precedence: explicit CLI > env vars > defaults.

### `ec2/minecraft/provision_servers.py`
Multi-server provisioning system. Runs on the EC2 instance as root. Reads a YAML configuration from a separate repository and generates per-server systemd service units, start/stop scripts, and `server.properties` files. Supports `--read-only` validation and separate `--update`/`--provision` phases.

### `ec2/minecraft/setup.sh`
First-boot server provisioner. Validates preconditions (persistent mount, required args), installs the JDK, downloads the server JAR, and registers the initial systemd service. Invoked via the `SetupCommand` CloudFormation parameter from UserData.

### `ec2/minecraft/start-minecraft.sh` and `stop-minecraft.sh`
systemd service wrappers. `start-minecraft.sh` validates EULA acceptance and server config before launching the JVM in a detached screen session with dated console logging. `stop-minecraft.sh` sends an in-game warning then `/stop`, giving players notice before the process exits.

### `ec2/minecraft/mcstatus.sh`
Quick operational status display. Lists all `minecraft-*.service` units with coloured status indicators (running / stopped / failed).

---

## Developer workflow

```bash
# One-time setup
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt

# Deploy or redeploy
python bin/reinstall_stack.py \
  --port-start 25565 \
  --port-end 25565 \
  --setup-command "./ec2/minecraft/setup.sh --server-folder=vanilla --server-version=1.21 \
    --jar-url=https://example.com/server.jar \
    --java-package=java-21-amazon-corretto-devel" \
  --instance-type t4g.medium
```

The script will:
- Find and offer to delete any existing `GameStack-*` stack
- Automatically adopt the previous stack's EBS volume ID (preserving world data)
- Show a full parameter summary before creating
- Wait for completion and print the server IP

Environment variables (`GAME_PORT_START`, `GAME_PORT_END`, `GAME_SETUP_COMMAND`, `GAME_EXISTING_VOLUME_ID`, `GAME_INSTANCE_TYPE`) can substitute for CLI arguments.

### After first deploy

```bash
ssh -i ~/.ssh/your-key.pem ec2-user@<ServerIP>

# Accept EULA (required before first start)
echo "eula=true" > /mnt/persist/minecraft/vanilla/eula.txt

# Start the server
sudo systemctl start minecraft-server

# Attach to the console
screen -r minecraft
```

---

## Repository layout

```
cloudformation_server_stack.yaml   Infrastructure template
requirements.txt                   Python dependencies (boto3, pyyaml)
bin/
  reinstall_stack.py               Stack lifecycle management (workstation)
ec2/
  update-release.sh                AL2023 release upgrade helper (ec2-user)
  minecraft/
    setup.sh                       First-boot provisioner (invoked by UserData)
    provision_servers.py           Multi-server systemd provisioner (EC2, root)
    mcstatus.sh                    Server status display (EC2)
    start-minecraft.sh             JVM launch wrapper (installed to server folder by setup.sh)
    stop-minecraft.sh              Graceful shutdown wrapper (installed to server folder by setup.sh)
```

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
