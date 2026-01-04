#!/usr/bin/env python3

"""
bin/reinstall_stack.py

Developer tool to accelerate iteration on the GameStack CloudFormation deployment.

Workflow:
1. Find active stacks in ap-southeast-4 whose name starts with 'GameStack'.
2. If exactly one exists → prompt to delete it.
3. If multiple → error and abort.
4. If none → proceed directly.
5. Delete confirmed stack and wait for DELETE_COMPLETE.
6. Determine volume handling and show user before creation confirmation.
7. Prompt to create a new stack.
8. Create a new timestamped stack using the local template.
9. Wait for CREATE_COMPLETE and display key outputs (ServerIP, etc.).

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

Key new feature:
  When ExistingVolumeId is non-empty (either explicit, reused, or from env),
  the script automatically detects the volume's Availability Zone using EC2 describe_volumes
  and passes it as a new CloudFormation parameter AvailabilityZone.
  This pins the EC2 instance to the correct AZ, preventing attachment failures.
"""

import argparse
import datetime
import os
import sys

import boto3
from botocore.exceptions import ClientError, WaiterError


REGION = "ap-southeast-4"
STACK_PREFIX = "GameStack"
TEMPLATE_PATH = "../cloudformation_server_stack.yaml"


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
            print(f"Warning: Volume {volume_id} not found in region {REGION}.")
            sys.exit(1)
        az = response["Volumes"][0]["AvailabilityZone"]
        return az
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidVolume.NotFound":
            print(f"Error: Volume {volume_id} does not exist or is not accessible.")
        else:
            print(f"Error describing volume {volume_id}: {e}")
        sys.exit(1)


def delete_stack(client, stack_name: str):
    """Delete the stack and wait for completion."""
    print(f"Deleting stack {stack_name}...")
    client.delete_stack(StackName=stack_name)
    waiter = client.get_waiter("stack_delete_complete")
    try:
        waiter.wait(StackName=stack_name)
        print(f"Stack {stack_name} deleted successfully.")
    except WaiterError as e:
        print(f"Error waiting for deletion of {stack_name}: {e}")
        sys.exit(1)


def create_stack(client, stack_name: str, template_body: str, parameters: list):
    """Create the stack and wait for completion."""
    print(f"Creating stack {stack_name}...")
    response = client.create_stack(
        StackName=stack_name,
        TemplateBody=template_body,
        Parameters=parameters,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        OnFailure="DELETE",
    )
    print("Creation initiated. Waiting for completion (this may take several minutes)...")
    waiter = client.get_waiter("stack_create_complete")
    try:
        waiter.wait(StackName=stack_name)
        print(f"Stack {stack_name} created successfully.")
    except WaiterError as e:
        print(f"Stack creation failed: {e}")
        # Fetch failure reason
        desc = client.describe_stacks(StackName=stack_name)
        events = client.describe_stack_events(StackName=stack_name)["StackEvents"]
        failed = [ev for ev in events if ev["ResourceStatus"].endswith("FAILED")]
        if failed:
            print("Recent failure events:")
            for ev in failed[:5]:
                print(f"  {ev['ResourceStatus']} - {ev['ResourceType']} - {ev.get('ResourceStatusReason', 'No reason')}")
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
    parser.add_argument("--existing-volume-id", type=str, default=None,
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
    args = parser.parse_args()

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
        print("Error: Multiple GameStack stacks found:")
        for s in stacks:
            print(f"  - {s['StackName']} ({s['StackStatus']})")
        print("Please clean up manually and try again.")
        sys.exit(1)

    if len(stacks) == 1:
        stack = stacks[0]
        print(f"Found existing stack: {stack['StackName']} ({stack['StackStatus']})")
        confirm = input("Delete this stack? Press Enter to continue or Ctrl+C to abort: ")
        if confirm != "":
            print("Aborted.")
            sys.exit(0)

        # Fetch parameters to possibly reuse ExistingVolumeId
        params = get_stack_parameters(cf_client, stack["StackName"])
        old_volume_id = params.get("ExistingVolumeId", "")
        deleted_stack_name = stack["StackName"]

        delete_stack(cf_client, deleted_stack_name)

    # Determine final ExistingVolumeId early, before creation confirmation
    final_volume_id = args.existing_volume_id

    if final_volume_id is not None:
        print(f"Using explicitly provided ExistingVolumeId: {final_volume_id}")
    elif args.reuse_volume and old_volume_id:
        print(f"Reusing ExistingVolumeId '{old_volume_id}' from previous stack {deleted_stack_name or ''}.")
        final_volume_id = old_volume_id
    else:
        print("No existing volume specified — a new EBS volume will be created.")
        final_volume_id = ""

    # Detect AvailabilityZone if we're using an existing volume
    availability_zone = None
    if final_volume_id:
        print(f"Detecting Availability Zone for volume {final_volume_id}...")
        availability_zone = get_volume_az(ec2_client, final_volume_id)
        print(f"Volume is in Availability Zone: {availability_zone}")
    else:
        print("New volume will be created — Availability Zone will be chosen automatically by AWS.")

    # Confirm creation with full visibility
    print("\nReady to create a new stack.")
    print(f"  Parameters summary:")
    print(f"    ServerPortNumberStart: {args.port_start}")
    print(f"    ServerPortNumberEnd: {args.port_end}")
    print(f"    SetupCommand: {args.setup_command}")
    print(f"    ExistingVolumeId: '{final_volume_id}'")
    print(f"    InstanceType: {args.instance_type}")
    if availability_zone:
        print(f"    AvailabilityZone: {availability_zone} (pinned to match volume)")
    else:
        print(f"    AvailabilityZone: (automatic selection)")
    confirm = input("\nProceed with stack creation? Press Enter to continue or Ctrl+C to abort: ")
    if confirm != "":
        print("Aborted.")
        sys.exit(0)

    # Build parameters list for CloudFormation
    parameters = [
        {"ParameterKey": "ServerPortNumberStart", "ParameterValue": str(args.port_start)},
        {"ParameterKey": "ServerPortNumberEnd", "ParameterValue": str(args.port_end)},
        {"ParameterKey": "SetupCommand", "ParameterValue": args.setup_command},
        {"ParameterKey": "ExistingVolumeId", "ParameterValue": final_volume_id},
        {"ParameterKey": "InstanceType", "ParameterValue": args.instance_type},
    ]
    if availability_zone:
        parameters.append({"ParameterKey": "AvailabilityZone", "ParameterValue": availability_zone})

    # Read template
    try:
        with open(TEMPLATE_PATH, "r") as f:
            template_body = f.read()
    except FileNotFoundError:
        print(f"Error: Template file not found at {TEMPLATE_PATH}")
        sys.exit(1)

    # Generate timestamped stack name
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    new_stack_name = f"{STACK_PREFIX}-{timestamp}"

    create_stack(cf_client, new_stack_name, template_body, parameters)

    # Display outputs
    outputs = get_stack_outputs(cf_client, new_stack_name)
    print("\nStack creation complete. Key outputs:")
    for key, value in outputs.items():
        print(f"  {key}: {value}")

    # Highlight the most useful one
    if "ServerIP" in outputs:
        print(f"\nConnect via SSH: ssh -i your-key.pem ec2-user@{outputs['ServerIP']}")


if __name__ == "__main__":
    main()
