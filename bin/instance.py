#!/usr/bin/env python3

"""
bin/instance.py

Workstation tool to start, stop, reboot, inspect, or SSH into the game server
EC2 instance.

The EC2 instance ID is resolved at call time from CloudFormation stack resources
— no hardcoded instance IDs. Shortcuts that invoke this script survive stack
reinstalls without modification.

Authentication: Uses standard AWS credential chain (~/.aws/credentials, env
vars, etc.). Optional --profile override provided.

Subcommands:
  start    Start the instance
  stop     Stop the instance
  reboot   Reboot the instance
  status   Show instance state, public IP, and uptime
  ssh      Open an SSH session to the instance (replaces this process via execvp)

--pause SECONDS
  Sleep N seconds after completion — useful for shortcut invocations where the
  terminal closes immediately on exit. Default 0 (no pause). Not applicable to
  the ssh subcommand, which replaces this process entirely.
"""

import argparse
import datetime
import logging
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError


REGION = "ap-southeast-4"
STACK_PREFIX = "GameStack"
ACTIVE_STATES = {"CREATE_COMPLETE", "UPDATE_COMPLETE"}
SSH_KEY_PATH = "~/.ssh/tim_ssh_to_game_server"
SSH_USER = "ec2-user"

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


def find_active_stack(cf) -> str:
    """Return the name of the single active GameStack, or exit on 0 or >1 matches."""
    paginator = cf.get_paginator("list_stacks")
    matching = []
    for page in paginator.paginate(StackStatusFilter=list(ACTIVE_STATES)):
        for stack in page.get("StackSummaries", []):
            if stack["StackName"].startswith(STACK_PREFIX):
                matching.append(stack["StackName"])

    if not matching:
        sys.exit("No active GameStack found.")

    if len(matching) > 1:
        logger.error("Multiple active GameStack stacks found:")
        for name in matching:
            logger.error("  - %s", name)
        sys.exit(1)

    return matching[0]


def get_instance_id(cf, stack_name: str) -> str:
    """Resolve the EC2 instance ID from the CloudFormation stack resource."""
    try:
        response = cf.describe_stack_resource(
            StackName=stack_name,
            LogicalResourceId="ServerInstance",
        )
        return response["StackResourceDetail"]["PhysicalResourceId"]
    except ClientError as e:
        logger.error("Failed to resolve instance ID from stack %s: %s", stack_name, e)
        sys.exit(1)


def get_stack_outputs(cf, stack_name: str) -> dict:
    """Return the outputs of a stack as a dict."""
    desc = cf.describe_stacks(StackName=stack_name)
    outputs = desc["Stacks"][0].get("Outputs", [])
    return {out["OutputKey"]: out["OutputValue"] for out in outputs}


def format_uptime(launch_time: datetime.datetime) -> str:
    """Format a duration from launch_time to now as 'Xd Yh Zm'."""
    delta = datetime.datetime.now(datetime.timezone.utc) - launch_time
    total_seconds = int(delta.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def cmd_start(ec2, instance_id: str) -> None:
    """Start the instance."""
    try:
        ec2.start_instances(InstanceIds=[instance_id])
        print(f"Start requested for {instance_id}.")
    except ClientError as e:
        logger.error("Failed to start %s: %s", instance_id, e)
        sys.exit(1)


def cmd_stop(ec2, instance_id: str) -> None:
    """Stop the instance."""
    try:
        ec2.stop_instances(InstanceIds=[instance_id])
        print(f"Stop requested for {instance_id}.")
    except ClientError as e:
        logger.error("Failed to stop %s: %s", instance_id, e)
        sys.exit(1)


def cmd_reboot(ec2, instance_id: str) -> None:
    """Reboot the instance."""
    try:
        ec2.reboot_instances(InstanceIds=[instance_id])
        print(f"Reboot requested for {instance_id}.")
    except ClientError as e:
        logger.error("Failed to reboot %s: %s", instance_id, e)
        sys.exit(1)


def cmd_status(ec2, instance_id: str) -> None:
    """Display instance state, public IP, and uptime."""
    try:
        response = ec2.describe_instances(InstanceIds=[instance_id])
    except ClientError as e:
        logger.error("Failed to describe instance %s: %s", instance_id, e)
        sys.exit(1)

    reservations = response.get("Reservations", [])
    if not reservations:
        logger.error("Instance %s not found.", instance_id)
        sys.exit(1)

    inst = reservations[0]["Instances"][0]
    state = inst["State"]["Name"]
    public_ip = inst.get("PublicIpAddress")
    launch_time = inst.get("LaunchTime")

    print(f"instance:  {instance_id}")
    print(f"state:     {state}")
    if public_ip:
        print(f"public ip: {public_ip}")
    if state == "running" and launch_time:
        print(f"uptime:    {format_uptime(launch_time)}")


def cmd_ssh(cf, stack_name: str) -> None:
    """Replace this process with an SSH session to the instance's Elastic IP.

    Uses the EIP from stack outputs (ServerIP) rather than the current public IP
    on the instance, so the address is stable across stop/start cycles.
    """
    try:
        outputs = get_stack_outputs(cf, stack_name)
    except ClientError as e:
        logger.error("Failed to retrieve stack outputs: %s", e)
        sys.exit(1)

    ip = outputs.get("ServerIP")
    if not ip:
        logger.error("ServerIP not found in stack outputs for %s.", stack_name)
        sys.exit(1)

    key_path = os.path.expanduser(SSH_KEY_PATH)
    # execvp replaces this process — no subprocess wrapper, no zombie, clean TTY
    os.execvp("ssh", ["ssh", "-i", key_path, f"{SSH_USER}@{ip}"])


def main():
    parser = argparse.ArgumentParser(
        description="Start, stop, reboot, inspect, or SSH into the game server EC2 instance.",
    )
    parser.add_argument("--profile", default="default", help="AWS profile name (default: default)")
    parser.add_argument(
        "--pause", type=int, default=0, metavar="SECONDS",
        help="Sleep N seconds after completion (useful for shortcut invocations "
             "where the terminal closes immediately on exit)",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    subparsers.add_parser("start", help="Start the instance")
    subparsers.add_parser("stop", help="Stop the instance")
    subparsers.add_parser("reboot", help="Reboot the instance")
    subparsers.add_parser("status", help="Show instance state, public IP, and uptime")
    subparsers.add_parser("ssh", help="Open an SSH session to the instance")

    args = parser.parse_args()

    cf = get_cf_client(args.profile)
    stack_name = find_active_stack(cf)

    if args.subcommand == "ssh":
        # execvp replaces this process — --pause is not reachable after this point
        cmd_ssh(cf, stack_name)
        sys.exit(1)  # unreachable if execvp succeeds

    ec2 = get_ec2_client(args.profile)
    instance_id = get_instance_id(cf, stack_name)

    if args.subcommand == "start":
        cmd_start(ec2, instance_id)
    elif args.subcommand == "stop":
        cmd_stop(ec2, instance_id)
    elif args.subcommand == "reboot":
        cmd_reboot(ec2, instance_id)
    elif args.subcommand == "status":
        cmd_status(ec2, instance_id)

    time.sleep(args.pause)


if __name__ == "__main__":
    main()
