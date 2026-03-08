#!/usr/bin/env python3

"""
bin/deploy_control_api.py

Deploy or update the GameControlApi CloudFormation stack, which exposes a Lambda
Function URL for starting and stopping the game server EC2 instance from mobile
home screen shortcuts.

The stack is stable (not reinstall-cycled) — it survives game server stack
reinstalls and is updated in place rather than deleted and recreated.

Authentication: Uses standard AWS credential chain (~/.aws/credentials, env
vars, etc.). Optional --profile override provided.

Safe by default: --execute required to write state. Without --execute the script
resolves all parameters and reports what would happen but makes no AWS state
changes.

API key handling:
  Pass --api-key on the command line, or omit it to be prompted at runtime
  (input is hidden via getpass). The key is never logged.
"""

import argparse
import getpass
import logging
import sys

import boto3
import yaml
from botocore.exceptions import ClientError, WaiterError


REGION = "ap-southeast-4"
STACK_NAME = "GameControlApi"
TEMPLATE_PATH = "../cloudformation_control_api_stack.yaml"

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


def describe_stack(cf) -> dict | None:
    """Return the stack description dict, or None if the stack does not exist."""
    try:
        response = cf.describe_stacks(StackName=STACK_NAME)
        return response["Stacks"][0]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            return None
        raise


def create_stack(cf, template_body: str, parameters: list) -> None:
    """Create the stack and wait for CREATE_COMPLETE."""
    logger.info("Creating stack %s...", STACK_NAME)
    cf.create_stack(
        StackName=STACK_NAME,
        TemplateBody=template_body,
        Parameters=parameters,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        OnFailure="DELETE",
    )
    logger.info("Waiting for CREATE_COMPLETE...")
    try:
        cf.get_waiter("stack_create_complete").wait(StackName=STACK_NAME)
        logger.info("Stack created.")
    except WaiterError as e:
        logger.error("Stack creation failed: %s", e)
        sys.exit(1)


def update_stack(cf, template_body: str, parameters: list) -> None:
    """Update the stack and wait for UPDATE_COMPLETE."""
    logger.info("Updating stack %s...", STACK_NAME)
    try:
        cf.update_stack(
            StackName=STACK_NAME,
            TemplateBody=template_body,
            Parameters=parameters,
            Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        )
    except ClientError as e:
        if "No updates are to be performed" in str(e):
            logger.info("Stack is already up to date — no changes.")
            return
        logger.error("Update failed: %s", e)
        sys.exit(1)
    logger.info("Waiting for UPDATE_COMPLETE...")
    try:
        cf.get_waiter("stack_update_complete").wait(StackName=STACK_NAME)
        logger.info("Stack updated.")
    except WaiterError as e:
        logger.error("Stack update failed: %s", e)
        sys.exit(1)


def get_stack_outputs(cf) -> dict:
    """Return the stack outputs as a dict."""
    response = cf.describe_stacks(StackName=STACK_NAME)
    outputs = response["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def main():
    parser = argparse.ArgumentParser(
        description="Deploy or update the GameControlApi Lambda stack for mobile instance control",
    )
    parser.add_argument("--profile", default="default", help="AWS profile name (default: default)")
    parser.add_argument(
        "--api-key", dest="api_key", default=None,
        help="API key for the control endpoint (prompted if omitted; input is hidden)",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually deploy. Default is a dry run — safe by default.",
    )
    args = parser.parse_args()

    # Resolve API key — prompt if not provided; never log the value
    api_key = args.api_key
    if not api_key:
        api_key = getpass.getpass("API key (input hidden): ")
    if not api_key:
        logger.error("API key must not be empty.")
        sys.exit(1)

    cf = get_cf_client(args.profile)

    existing = describe_stack(cf)
    if existing:
        logger.info("Found existing stack: %s (%s)", STACK_NAME, existing["StackStatus"])
        operation = "update"
    else:
        logger.info("No existing stack found — will create.")
        operation = "create"

    parameters = [{"ParameterKey": "ApiKey", "ParameterValue": api_key}]

    # Parameter summary — key intentionally omitted from all log output
    logger.info("Ready to %s stack %s.", operation, STACK_NAME)
    logger.info("  Parameters:")
    logger.info("    ApiKey: <hidden>")

    if not args.execute:
        result = {
            "dry_run": True,
            "operation": operation,
            "stack_name": STACK_NAME,
        }
        print(yaml.dump(result, default_flow_style=False, sort_keys=False))
        return

    # Read template — deferred until after dry-run exit to avoid unnecessary I/O on abort
    try:
        with open(TEMPLATE_PATH) as f:
            template_body = f.read()
    except FileNotFoundError:
        logger.error("Template not found at %s", TEMPLATE_PATH)
        sys.exit(1)

    if operation == "create":
        create_stack(cf, template_body, parameters)
    else:
        update_stack(cf, template_body, parameters)

    outputs = get_stack_outputs(cf)
    result = {"stack_name": STACK_NAME, "outputs": outputs}
    print(yaml.dump(result, default_flow_style=False, sort_keys=False))

    function_url = outputs.get("FunctionUrl", "")
    if function_url:
        logger.info("Start shortcut URL: %s?action=start&key=<your-key>", function_url)
        logger.info("Stop shortcut URL:  %s?action=stop&key=<your-key>", function_url)


if __name__ == "__main__":
    main()
