#!/usr/bin/env python3

"""
bin/reinstall_stack.py

Developer tool to accelerate iteration on the GameStack CloudFormation deployment.

Default workflow (delete + create):
1. Find active stacks in ap-southeast-4 whose name starts with 'GameStack'.
2. If exactly one exists → prompt to delete it (skipped with --yes or --dry-run).
3. If multiple → error and abort.
4. If none → proceed directly.
5. Delete confirmed stack and wait for DELETE_COMPLETE.
6. Look up VolumeId, AllocationId, and PublicIp from the persistent stack.
7. Derive AvailabilityZone from the volume (ec2.describe_volumes).
8. Prompt to create a new stack (skipped with --yes or --dry-run).
9. Create a new timestamped stack using the local template.
10. Wait for CREATE_COMPLETE and emit key outputs as YAML on stdout.

Delete-only workflow (--delete-only):
  Steps 1–5 only. Exits after DELETE_COMPLETE without contacting the persistent stack
  or creating a new game stack. Use when tearing down without an immediate redeploy,
  e.g. during a first-time migration to the persistent stack architecture.

Authentication: Uses standard AWS credential chain (~/.aws/credentials, env vars, etc.).
Optional --profile override provided.

Persistent stack: VolumeId and AllocationId are sourced from GamePersistentStack (or the
stack named by GAME_PERSISTENT_STACK / --persistent-stack). The persistent stack must exist
before running this script in default mode. Use bin/setup_persistent_stack.py to create it.
Not accessed in --delete-only mode.

Environment variable support:
  GAME_PORT_START           → --port-start
  GAME_PORT_END             → --port-end
  GAME_SETUP_COMMAND        → --setup-command (required in default mode unless set here)
  GAME_INSTANCE_TYPE        → --instance-type
  GAME_PERSISTENT_STACK     → --persistent-stack

Explicit CLI arguments always override environment variables.

Safe by default: --execute required to write state
  Without --execute the script resolves all parameters and reports what would happen,
  but makes no AWS state changes. This is the default mode.
  Pass --execute to actually delete/create stacks.

Confirmation skipping: --yes / -y
  Skip both interactive confirmation prompts when running with --execute.
  Intended for agentic or CI use. The parameter summary is still printed to stderr.

Output:
  Status and progress messages → stderr (via logger).
  Structured result (stack outputs or dry-run parameters) → stdout as YAML.
"""

import argparse
import datetime
import logging
import os
import sys

import boto3
import yaml
from botocore.exceptions import ClientError, WaiterError


REGION = "ap-southeast-4"
STACK_PREFIX = "GameStack"
DEFAULT_PERSISTENT_STACK = "GamePersistentStack"
# Resolved relative to this script file so the script can be invoked from any directory.
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cloudformation_server_stack.yaml")

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def get_cf_client(profile: str):
    """Create a CloudFormation client using the specified profile."""
    session = boto3.Session(profile_name=profile)
    return session.client("cloudformation", region_name=REGION)


def get_ec2_client(profile: str):
    """Create an EC2 client using the specified profile."""
    session = boto3.Session(profile_name=profile)
    return session.client("ec2", region_name=REGION)


def find_game_stacks(client):
    """Return a list of active stacks matching the prefix."""
    paginator = client.get_paginator("list_stacks")
    matching = []
    for page in paginator.paginate(StackStatusFilter=["CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE", "CREATE_FAILED", "UPDATE_FAILED", "ROLLBACK_COMPLETE"]):
        for stack in page.get("StackSummaries", []):
            if stack["StackName"].startswith(STACK_PREFIX):
                matching.append(stack)
    return matching


def get_persistent_stack_outputs(cf_client, stack_name: str) -> dict:
    """Return outputs of the persistent stack as a dict; exit with a clear message if absent."""
    try:
        desc = cf_client.describe_stacks(StackName=stack_name)
    except ClientError as e:
        if "does not exist" in str(e):
            logger.error(
                "Persistent stack '%s' does not exist. "
                "Run bin/setup_persistent_stack.py to create it first.",
                stack_name,
            )
            sys.exit(1)
        raise
    outputs = desc["Stacks"][0].get("Outputs", [])
    return {out["OutputKey"]: out["OutputValue"] for out in outputs}


def get_volume_az(ec2_client, volume_id: str) -> str:
    """Return the Availability Zone of the given volume ID."""
    try:
        response = ec2_client.describe_volumes(VolumeIds=[volume_id])
        if not response["Volumes"]:
            logger.error("Volume %s not found in region %s.", volume_id, REGION)
            sys.exit(1)
        return response["Volumes"][0]["AvailabilityZone"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidVolume.NotFound":
            logger.error("Volume %s does not exist or is not accessible.", volume_id)
        else:
            logger.error("Error describing volume %s: %s", volume_id, e)
        sys.exit(1)


def delete_stack(client, stack_name: str, dry_run: bool = False) -> None:
    """Delete the stack and wait for completion.

    dry_run: log intent and return without calling the AWS API.
    Gate is placed immediately before the state-writing call.
    """
    logger.info("Deleting stack %s...", stack_name)
    if dry_run:
        logger.info("[DRY RUN] Would call client.delete_stack('%s') — skipping.", stack_name)
        return
    client.delete_stack(StackName=stack_name)
    waiter = client.get_waiter("stack_delete_complete")
    try:
        waiter.wait(StackName=stack_name)
        logger.info("Stack %s deleted successfully.", stack_name)
    except WaiterError as e:
        logger.error("Error waiting for deletion of %s: %s", stack_name, e)
        sys.exit(1)


def create_stack(client, stack_name: str, template_body: str, parameters: list,
                 dry_run: bool = False) -> None:
    """Create the stack and wait for completion.

    dry_run: log intent and return without calling the AWS API.
    Gate is placed immediately before the state-writing call.
    """
    logger.info("Creating stack %s...", stack_name)
    if dry_run:
        logger.info("[DRY RUN] Would call client.create_stack('%s') — skipping.", stack_name)
        return
    client.create_stack(
        StackName=stack_name,
        TemplateBody=template_body,
        Parameters=parameters,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        OnFailure="DELETE",
    )
    logger.info("Creation initiated. Waiting for completion (this may take several minutes)...")
    waiter = client.get_waiter("stack_create_complete")
    try:
        waiter.wait(StackName=stack_name)
        logger.info("Stack %s created successfully.", stack_name)
    except WaiterError as e:
        logger.error("Stack creation failed: %s", e)
        events = client.describe_stack_events(StackName=stack_name)["StackEvents"]
        failed = [ev for ev in events if ev["ResourceStatus"].endswith("FAILED")]
        if failed:
            logger.error("Recent failure events:")
            for ev in failed[:5]:
                logger.error("  %s - %s - %s", ev["ResourceStatus"], ev["ResourceType"],
                             ev.get("ResourceStatusReason", "No reason"))
        sys.exit(1)


def get_stack_outputs(client, stack_name: str):
    """Return the outputs of a completed stack as a dict."""
    desc = client.describe_stacks(StackName=stack_name)
    outputs = desc["Stacks"][0].get("Outputs", [])
    return {out["OutputKey"]: out["OutputValue"] for out in outputs}


def main():
    # Load defaults from environment variables
    env_defaults = {
        "port_start": os.getenv("GAME_PORT_START"),
        "port_end": os.getenv("GAME_PORT_END"),
        "setup_command": os.getenv("GAME_SETUP_COMMAND"),
        "instance_type": os.getenv("GAME_INSTANCE_TYPE"),
        "persistent_stack": os.getenv("GAME_PERSISTENT_STACK", DEFAULT_PERSISTENT_STACK),
    }

    parser = argparse.ArgumentParser(description="Reinstall GameStack CloudFormation stack for rapid iteration")
    parser.add_argument("--port-start", type=int, default=25565 if not env_defaults["port_start"] else int(env_defaults["port_start"]),
                        help="ServerPortNumberStart (default: 25565 or GAME_PORT_START)")
    parser.add_argument("--port-end", type=int, default=None,
                        help="ServerPortNumberEnd (default: same as port-start or GAME_PORT_END)")
    parser.add_argument("--setup-command", type=str,
                        default=env_defaults["setup_command"],
                        help="Full SetupCommand string (required in default mode unless GAME_SETUP_COMMAND is set)")
    parser.add_argument("--instance-type", type=str,
                        default="t4g.medium" if not env_defaults["instance_type"] else env_defaults["instance_type"],
                        help="InstanceType (default: t4g.medium or GAME_INSTANCE_TYPE)")
    parser.add_argument("--persistent-stack", type=str,
                        default=env_defaults["persistent_stack"],
                        help=f"Persistent stack name (default: GAME_PERSISTENT_STACK or '{DEFAULT_PERSISTENT_STACK}')")
    parser.add_argument("--profile", type=str, default="default", help="AWS profile name (default: default)")
    parser.add_argument("--delete-only", action="store_true", dest="delete_only",
                        help="Delete the existing game stack and exit without creating a new one.")
    parser.add_argument("--yes", "-y", action="store_true", dest="yes",
                        help="Skip interactive confirmations when running with --execute (for agentic or CI use)")
    parser.add_argument("--execute", action="store_true", dest="execute",
                        help="Actually perform AWS state changes (delete/create). Default is a dry run — safe by default.")
    args = parser.parse_args()

    # Without --execute the script is non-destructive — no interactive prompts needed
    if not args.execute:
        args.yes = True

    # --setup-command is only required in default (delete + create) mode
    if not args.delete_only and not args.setup_command:
        parser.error("--setup-command is required in default mode (or set GAME_SETUP_COMMAND)")

    # Resolve port-end: CLI > env > port-start
    if args.port_end is None:
        if env_defaults["port_end"]:
            args.port_end = int(env_defaults["port_end"])
        else:
            args.port_end = args.port_start

    cf_client = get_cf_client(args.profile)
    ec2_client = get_ec2_client(args.profile)

    # Find existing game stacks
    stacks = find_game_stacks(cf_client)

    deleted_stack_name = None

    if len(stacks) > 1:
        logger.error("Multiple GameStack stacks found:")
        for s in stacks:
            logger.error("  - %s (%s)", s["StackName"], s["StackStatus"])
        logger.error("Please clean up manually and try again.")
        sys.exit(1)

    if len(stacks) == 1:
        stack = stacks[0]
        logger.info("Found existing stack: %s (%s)", stack["StackName"], stack["StackStatus"])
        if not args.yes:
            confirm = input("Delete this stack? Press Enter to continue or Ctrl+C to abort: ")
            if confirm != "":
                print("Aborted.")
                sys.exit(0)

        deleted_stack_name = stack["StackName"]
        delete_stack(cf_client, deleted_stack_name, dry_run=not args.execute)

    if args.delete_only:
        result = {"delete_only": True, "deleted": deleted_stack_name, "dry_run": not args.execute}
        print(yaml.dump(result, default_flow_style=False, sort_keys=False))
        return

    # Source VolumeId and AllocationId from the persistent stack
    logger.info("Reading outputs from persistent stack '%s'...", args.persistent_stack)
    persistent_outputs = get_persistent_stack_outputs(cf_client, args.persistent_stack)

    volume_id = persistent_outputs.get("VolumeId")
    allocation_id = persistent_outputs.get("AllocationId")
    public_ip = persistent_outputs.get("PublicIp")

    if not volume_id:
        logger.error("VolumeId not found in persistent stack outputs. Has setup_persistent_stack.py been run?")
        sys.exit(1)
    if not allocation_id:
        logger.error("AllocationId not found in persistent stack outputs.")
        sys.exit(1)

    logger.info("Persistent stack outputs — VolumeId: %s  AllocationId: %s  PublicIp: %s",
                volume_id, allocation_id, public_ip)

    # Derive AvailabilityZone from the volume to pin the instance to the correct AZ
    logger.info("Detecting Availability Zone for volume %s...", volume_id)
    availability_zone = get_volume_az(ec2_client, volume_id)
    logger.info("Volume is in Availability Zone: %s", availability_zone)

    parameters = [
        {"ParameterKey": "ServerPortNumberStart", "ParameterValue": str(args.port_start)},
        {"ParameterKey": "ServerPortNumberEnd", "ParameterValue": str(args.port_end)},
        {"ParameterKey": "SetupCommand", "ParameterValue": args.setup_command},
        {"ParameterKey": "PersistentStackName", "ParameterValue": args.persistent_stack},
        {"ParameterKey": "InstanceType", "ParameterValue": args.instance_type},
        {"ParameterKey": "AvailabilityZone", "ParameterValue": availability_zone},
    ]

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    new_stack_name = f"{STACK_PREFIX}-{timestamp}"

    # Parameter summary — always emitted to stderr regardless of dry-run or --yes
    logger.info("Ready to create stack %s.", new_stack_name)
    logger.info("  PersistentStackName:   %s", args.persistent_stack)
    logger.info("  PublicIp:              %s  (stable across reinstalls)", public_ip)
    logger.info("  AvailabilityZone:      %s  (derived from persistent volume)", availability_zone)
    logger.info("  ServerPortNumberStart: %s", args.port_start)
    logger.info("  ServerPortNumberEnd:   %s", args.port_end)
    logger.info("  SetupCommand:          %s", args.setup_command)
    logger.info("  InstanceType:          %s", args.instance_type)

    # Not executing: emit structured result and stop before any remaining state writes
    if not args.execute:
        result = {
            "dry_run": True,
            "would_delete": deleted_stack_name,
            "new_stack_name": new_stack_name,
            "persistent_stack": args.persistent_stack,
            "parameters": {p["ParameterKey"]: p["ParameterValue"] for p in parameters},
        }
        print(yaml.dump(result, default_flow_style=False, sort_keys=False))
        return

    if not args.yes:
        confirm = input("\nProceed with stack creation? Press Enter to continue or Ctrl+C to abort: ")
        if confirm != "":
            print("Aborted.")
            sys.exit(0)

    # Read template — deferred until after confirmation to avoid unnecessary I/O on abort
    try:
        with open(TEMPLATE_PATH, "r") as f:
            template_body = f.read()
    except FileNotFoundError:
        logger.error("Template file not found at %s", TEMPLATE_PATH)
        sys.exit(1)

    create_stack(cf_client, new_stack_name, template_body, parameters)

    # Emit stack outputs as structured YAML on stdout
    outputs = get_stack_outputs(cf_client, new_stack_name)
    result = {
        "stack_name": new_stack_name,
        "outputs": outputs,
    }
    print(yaml.dump(result, default_flow_style=False, sort_keys=False))

    if "ServerIP" in outputs:
        logger.info("Connect via SSH: ssh -i ~/.ssh/tim_ssh_to_game_server ec2-user@%s", outputs["ServerIP"])


if __name__ == "__main__":
    main()
