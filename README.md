# AWS-Games

Experiments defining game host stacks with AWS CloudFormation, focused on provisioning persistent EC2-based game servers (primarily Minecraft) on AWS.

## Overview

This repository provides:
- A CloudFormation template (cloudformation_server_stack.yaml) to deploy an Amazon Linux 2023 EC2 instance with:
  - Optional new or existing EBS volume for persistence (mounted at /mnt/persist).
  - Elastic IP for static addressing.
  - Security group allowing SSH and customizable game port range.
  - UserData bootstrap that formats/mounts storage, clones this repo, and executes a setup command.
- Shell scripts for server setup and management:
  - Root-level setup.sh: Generic bootstrap (relocated from minecraft/ for future multi-game support); configures Minecraft via arguments.
  - minecraft/start-minecraft.sh and minecraft/stop-minecraft.sh: systemd wrappers using screen for detached runtime.
  - Root-level update-release.sh: Helper for ec2-user to check and upgrade AL2023 release versions.
- Developer tools:
  - bin/reinstall_stack.py: Python script to accelerate iteration by deleting (if exists) and recreating the CloudFormation stack.

The design emphasizes persistence (data survives stops/terminations via EBS retain policy), cost awareness (e.g., t4g.medium default), and manual operations where appropriate (e.g., EULA acceptance).

## Deployment

1. Launch the stack via AWS Console or the provided script.
2. Key parameters:
   - InstanceType: Default t4g.medium (Graviton ARM, cost-effective).
   - ServerPortNumberStart/End: Game traffic ports (e.g., 25565 for Minecraft).
   - SetupCommand: Invocation like ./setup.sh --server-folder=<name> --server-version=<ver> --jar-url=<url> --java-package=<pkg>.
   - ExistingVolumeId: Empty for new volume, or provide ID to reuse.
3. Post-launch: SSH in, accept EULA (/mnt/persist/minecraft/<folder>/eula.txt), start service (sudo systemctl start minecraft-server).

## Developer Workflow (Iteration)

Use the local tool for rapid testing:

```bash
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt

python bin/reinstall_stack.py \
  --port-start 25565 \
  --port-end 25565 \
  --setup-command "./setup.sh --server-folder=vanilla --server-version=1.21 --jar-url=https://example.com/server.jar --java-package=java-21-amazon-corretto-devel" \
  [--existing-volume-id vol-xxx] \
  [--instance-type t4g.large]
```

The script handles deletion of prior GameStack-* stacks and creates a timestamped new one.

## Files

- cloudformation_server_stack.yaml: Main template.
- setup.sh: Root bootstrap (invoked via SetupCommand).
- update-release.sh: Release upgrade helper.
- minecraft/: Game-specific scripts (setup legacy, wrappers).
- bin/reinstall_stack.py: Stack management tool.
- requirements.txt: Python dependencies (boto3).

## License

Apache License 2.0 (see LICENSE).

---
Repository is early-stage; contributions welcome for multi-game support, backups, or monitoring.