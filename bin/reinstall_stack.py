#!/usr/bin/env python3

"""
bin/reinstall_stack.py

Developer tool to accelerate iteration on the GameStack CloudFormation deployment.

Workflow:
1. Find active stacks in ap-southeast-4 whose name starts with 'GameStack'.
2. If exactly one exists → prompt to delete it (skipped with --yes or --dry-run).
3. If multiple → error and abort.
4. If none → proceed directly.
5. Delete confirmed stack and wait for DELETE_COMPLETE.
6. Determine volume handling and resolve all parameters.
7. Prompt to create a new stack (skipped with --yes or --dry-run).
8. Create a new timestamped stack using the local template.
9. Wait for CREATE_COMPLETE and emit key outputs as JSON on stdout.

Authentication: Uses standard AWS credential chain (~/.aws/credentials, env vars, etc.).
Optional --profile override provided.

Volume reuse: --reuse-existing-volume / --no-reuse-existing-volume (default: reuse)
  If an existing stack is found and deleted, and --existing-volume-id is not explicitly
  provided, the script can automatically adopt the ExistingVolumeId parameter value
  from the old stack (if present in its parameters).

Environment variable support:
  GAME_PORT_START           → --port-start
  GAME_PORT_END             → --port-end
  GAME_SETUP_COMMAND        → --setup-command (required unless provided)
  GAME_EXISTING_VOLUME_ID   → --existing-volume-id
  GAME_INSTANCE_TYPE        → --instance-type

Explicit CLI arguments always override environment variables.

AZ pinning:
  When ExistingVolumeId is non-empty (either explicit, reused, or from env),
  the script automatically detects the volume's Availability Zone using EC2 describe_volumes
  and passes it as a new CloudFormation parameter AvailabilityZone.
  This pins the EC2 instance to the correct AZ, preventing attachment failures.

Safe by default: --execute required to write state
  Without --execute the script resolves all parameters and reports what would happen,
  but makes no AWS state changes. This is the default mode.
  Pass --execute to actually delete/create stacks.

Confirmation skipping: --yes / -y
  Skip both interactive confirmation prompts when running with --execute.
  Intended for agentic or CI use. The parameter summary is still printed to stderr.

Output:
  Status and progress messages → stderr (via logger).
  Structured result (stack outputs or dry-run parameters) → stdout as JSON.
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
TEMPLATE_PATH = "../cloudformation_server_stack.yaml"

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
    for page in paginator.paginate(StackStatusFilter=["CREATE_COMPLETE", "UPDATE_COMPLETE", "CREATE_FAILED", "UPDATE_FAILED", "ROLLBACK_COMPLETE"]):
        for stack in page.get("StackSummaries", []):
            if stack["StackName"].startswith(STACK_PREFIX):
                matching.append(stack)
    return matching


def get_stack_parameters(client, stack_name: str) -> dict:
    """Return a dict of ParameterKey → ParameterValue for the given stack."""
    desc = client.describe_stacks(StackName=stack_name)
    params = desc["Stacks"][0].get("Parameters", [])
    return {p["ParameterKey"]: p["ParameterValue"] for p in params}


def get_volume_az(ec2_client, volume_id: str) -> str:
    """Return the Availability Zone of the given volume ID."""
    try:
        response = ec2_client.describe_volumes(VolumeIds=[volume_id])
        if not response["Volumes"]:
            logger.error("Volume %s not found in region %s.", volume_id, REGION)
            sys.exit(1)
        az = response["Volumes"][0]["AvailabilityZone"]
        return az
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
        "existing_volume_id": os.getenv("GAME_EXISTING_VOLUME_ID"),
        "instance_type": os.getenv("GAME_INSTANCE_TYPE"),
    }

    parser = argparse.ArgumentParser(description="Reinstall GameStack CloudFormation stack for rapid iteration")
    parser.add_argument("--port-start", type=int, default=25565 if not env_defaults["port_start"] else int(env_defaults["port_start"]),
                        help="ServerPortNumberStart (default: 25565 or GAME_PORT_START)")
    parser.add_argument("--port-end", type=int, default=None,
                        help="ServerPortNumberEnd (default: same as port-start or GAME_PORT_END)")
    parser.add_argument("--setup-command", type=str,
                        default=env_defaults["setup_command"],
                        required=(not env_defaults["setup_command"]),
                        help="Full SetupCommand string (required unless GAME_SETUP_COMMAND is set)")
    parser.add_argument("--existing-volume-id", type=str, default=env_defaults["existing_volume_id"],
                        help="ExistingVolumeId; if omitted and --reuse-existing-volume is true, adopt from previous stack "
                             "(or use GAME_EXISTING_VOLUME_ID)")
    parser.add_argument("--instance-type", type=str,
                        default="t4g.medium" if not env_defaults["instance_type"] else env_defaults["instance_type"],
                        help="InstanceType (default: t4g.medium or GAME_INSTANCE_TYPE)")
    parser.add_argument("--profile", type=str, default="default", help="AWS profile name (default: default)")
    parser.add_argument("--reuse-existing-volume", action="store_true", dest="reuse_volume",
                        help="Automatically reuse ExistingVolumeId from deleted stack (default)")
    parser.add_argument("--no-reuse-existing-volume", action="store_false", dest="reuse_volume",
                        help="Do not reuse ExistingVolumeId from previous stack")
    parser.set_defaults(reuse_volume=True)
    parser.add_argument("--yes", "-y", action="store_true", dest="yes",
                        help="Skip interactive confirmations when running with --execute (for agentic or CI use)")
    parser.add_argument("--execute", action="store_true", dest="execute",
                        help="Actually perform AWS state changes (delete/create). Default is a dry run — safe by default.")
    args = parser.parse_args()

    # Without --execute the script is non-destructive — no interactive prompts needed
    if not args.execute:
        args.yes = True

    # Resolve port-end: CLI > env > port-start
    if args.port_end is None:
        if env_defaults["port_end"]:
            args.port_end = int(env_defaults["port_end"])
        else:
            args.port_end = args.port_start

    cf_client = get_cf_client(args.profile)
    ec2_client = get_ec2_client(args.profile)

    # Find existing stacks
    stacks = find_game_stacks(cf_client)

    old_volume_id = None
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

        # Fetch parameters to possibly reuse ExistingVolumeId
        params = get_stack_parameters(cf_client, stack["StackName"])
        old_volume_id = params.get("ExistingVolumeId", "")
        deleted_stack_name = stack["StackName"]

        delete_stack(cf_client, deleted_stack_name, dry_run=not args.execute)

    # Determine final ExistingVolumeId
    final_volume_id = args.existing_volume_id

    if final_volume_id is not None:
        logger.info("Using explicitly provided ExistingVolumeId: %s", final_volume_id)
    elif args.reuse_volume and old_volume_id:
        logger.info("Reusing ExistingVolumeId '%s' from previous stack %s.", old_volume_id, deleted_stack_name or "")
        final_volume_id = old_volume_id
    else:
        logger.info("No existing volume specified — a new EBS volume will be created.")
        final_volume_id = ""

    # Detect AvailabilityZone if we're using an existing volume
    availability_zone = None
    if final_volume_id:
        logger.info("Detecting Availability Zone for volume %s...", final_volume_id)
        availability_zone = get_volume_az(ec2_client, final_volume_id)
        logger.info("Volume is in Availability Zone: %s", availability_zone)
    else:
        logger.info("New volume will be created — Availability Zone will be chosen automatically by AWS.")

    # Build parameters list and stack name before the summary so dry-run has the full picture
    parameters = [
        {"ParameterKey": "ServerPortNumberStart", "ParameterValue": str(args.port_start)},
        {"ParameterKey": "ServerPortNumberEnd", "ParameterValue": str(args.port_end)},
        {"ParameterKey": "SetupCommand", "ParameterValue": args.setup_command},
        {"ParameterKey": "ExistingVolumeId", "ParameterValue": final_volume_id},
        {"ParameterKey": "InstanceType", "ParameterValue": args.instance_type},
    ]
    if availability_zone:
        parameters.append({"ParameterKey": "AvailabilityZone", "ParameterValue": availability_zone})

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    new_stack_name = f"{STACK_PREFIX}-{timestamp}"

    # Parameter summary — always emitted to stderr regardless of dry-run or --yes
    logger.info("Ready to create stack %s.", new_stack_name)
    logger.info("  Parameters:")
    logger.info("    ServerPortNumberStart: %s", args.port_start)
    logger.info("    ServerPortNumberEnd:   %s", args.port_end)
    logger.info("    SetupCommand:          %s", args.setup_command)
    logger.info("    ExistingVolumeId:      '%s'", final_volume_id)
    logger.info("    InstanceType:          %s", args.instance_type)
    if availability_zone:
        logger.info("    AvailabilityZone:      %s (pinned to match volume)", availability_zone)
    else:
        logger.info("    AvailabilityZone:      (automatic selection)")

    # Not executing: emit structured result and stop before any remaining state writes
    if not args.execute:
        result = {
            "dry_run": True,
            "would_delete": deleted_stack_name,
            "new_stack_name": new_stack_name,
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

    # Emit stack outputs as structured JSON on stdout
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
