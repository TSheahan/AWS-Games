#!/usr/bin/env python3

"""
bin/setup_persistent_stack.py

One-time setup tool for the GamePersistentStack — the singleton CloudFormation stack that
owns the EBS volume and Elastic IP that survive game server reinstalls.

Workflow: Create mode (no --import-* flags)
1. Require --availability-zone (no volume to derive it from)
2. Dry-run: report parameters; with --execute: create_stack, wait for CREATE_COMPLETE
3. Print outputs as YAML on stdout

Workflow: Import mode (one or both --import-* flags)
1. Call describe_volumes / describe_addresses to read actual resource properties
2. Derive AvailabilityZone from the imported volume (or require --availability-zone if
   importing EIP only)
3. Build template parameters that match actual resource properties (CFN import validation
   requires parameter values to match the physical resource)
4a. Full import (both resources): create_change_set with ChangeSetType=IMPORT; execute;
    wait for IMPORT_COMPLETE.
4b. Partial import (one resource imported, one created fresh): two-step —
    create_stack with a partial template containing only the fresh resource, then
    IMPORT changeset on the existing stack to bring in the imported resource.
    Required because CFN IMPORT on a new stack demands ALL template resources be in
    ResourcesToImport; mixing imported and fresh resources is not permitted in one step.
5. Print outputs as YAML on stdout

Authentication: Uses standard AWS credential chain. Optional --profile override.

Safe by default: --execute required to write state. Without it the script resolves all
parameters and reports what would happen.

Confirmation skipping: --yes / -y skips interactive prompts when running with --execute.

Output:
  Status and progress messages → stderr (via logger)
  Structured result (stack outputs or dry-run parameters) → stdout as YAML
"""

import argparse
import copy
import logging
import os
import sys
import time

import boto3
import yaml
from botocore.exceptions import ClientError, WaiterError


REGION = "ap-southeast-4"
DEFAULT_STACK_NAME = "GamePersistentStack"
# Resolved relative to this script file so the script can be invoked from any directory.
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "persistent-resources.yaml")

# Maps each logical resource ID to the output keys it owns in persistent-resources.yaml.
# Used to build partial templates when some resources are created fresh and others imported.
_RESOURCE_OUTPUTS = {
    "PersistentVolume": {"VolumeId"},
    "PersistentEIP": {"AllocationId", "PublicIp"},
}


class _CfnTag:
    """Wraps a CloudFormation intrinsic function tag and its value for YAML round-trips.

    PyYAML's safe_load rejects unknown tags like !Ref, !Sub, !GetAtt. This class lets a
    custom loader accept them as opaque objects and a custom dumper write them back
    faithfully, so CFN templates can be parsed, structurally modified, and re-serialised
    without corrupting any intrinsic function expressions.
    """

    def __init__(self, tag, value):
        self.tag = tag
        self.value = value


def _cfn_tag_constructor(loader, tag_suffix, node):
    """Multi-constructor: accepts any !Tag and wraps it in _CfnTag.

    tag_suffix is the portion after the registered prefix ('!'), e.g. 'Ref' for !Ref.
    We use node.tag instead, which carries the full tag string ('!Ref'), so the dumper
    can emit it back in short form rather than the verbatim form !<Ref>.
    """
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return _CfnTag(node.tag, value)


def _cfn_tag_representer(dumper, data):
    """Representer: serialises _CfnTag back to its original !Tag form."""
    if isinstance(data.value, str):
        return dumper.represent_scalar(data.tag, data.value)
    if isinstance(data.value, list):
        return dumper.represent_sequence(data.tag, data.value)
    return dumper.represent_mapping(data.tag, data.value)


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader extended to pass through CloudFormation intrinsic function tags."""


class _CfnDumper(yaml.Dumper):
    """Dumper that serialises _CfnTag objects back to their original YAML tag form."""


_CfnLoader.add_multi_constructor("!", _cfn_tag_constructor)
_CfnDumper.add_representer(_CfnTag, _cfn_tag_representer)

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


def read_template() -> str:
    """Read and return the persistent-resources.yaml template body."""
    try:
        with open(TEMPLATE_PATH, "r") as f:
            return f.read()
    except FileNotFoundError:
        logger.error("Template file not found at %s", TEMPLATE_PATH)
        sys.exit(1)


def build_partial_template(template_body: str, resource_ids: set,
                           output_resource_ids: set = None) -> str:
    """Return a template body containing only the specified resource IDs.

    resource_ids: logical resource IDs to include in the Resources section; all others
        are removed.
    output_resource_ids: controls which outputs are retained. Only outputs belonging to
        resources in this set are kept. Defaults to resource_ids when not provided.
        Pass a smaller set to suppress outputs for resources that exist in the stack but
        whose outputs are not yet present — e.g. when building the IMPORT changeset
        template in the mixed import flow (CFN forbids adding outputs in an IMPORT
        changeset, so newly-imported resource outputs must be withheld until a subsequent
        regular update).

    Uses _CfnLoader/_CfnDumper to preserve CloudFormation intrinsic function tags
    (!Ref, !Sub, !GetAtt, etc.) through the parse-modify-serialise round-trip.
    """
    if output_resource_ids is None:
        output_resource_ids = resource_ids
    template = yaml.load(template_body, Loader=_CfnLoader)
    partial = copy.deepcopy(template)
    resources_to_remove = set(partial.get("Resources", {}).keys()) - resource_ids
    for rid in resources_to_remove:
        partial["Resources"].pop(rid, None)
    # Remove outputs for any resource not in output_resource_ids, regardless of whether
    # the resource itself is present in the template.
    for rid, output_keys in _RESOURCE_OUTPUTS.items():
        if rid not in output_resource_ids:
            for output_key in output_keys:
                partial.get("Outputs", {}).pop(output_key, None)
    return yaml.dump(partial, Dumper=_CfnDumper, default_flow_style=False)


def get_stack_outputs(cf_client, stack_name: str) -> dict:
    """Return the outputs of a completed stack as a dict."""
    desc = cf_client.describe_stacks(StackName=stack_name)
    outputs = desc["Stacks"][0].get("Outputs", [])
    return {out["OutputKey"]: out["OutputValue"] for out in outputs}


def stack_exists(cf_client, stack_name: str) -> bool:
    """Return True if the stack exists in a non-deleted state."""
    try:
        desc = cf_client.describe_stacks(StackName=stack_name)
        status = desc["Stacks"][0]["StackStatus"]
        return not status.endswith("_COMPLETE") or status != "DELETE_COMPLETE"
    except ClientError as e:
        if "does not exist" in str(e):
            return False
        raise


def describe_volume(ec2_client, volume_id: str) -> dict:
    """Return the describe_volumes response for a single volume; exit on error."""
    try:
        resp = ec2_client.describe_volumes(VolumeIds=[volume_id])
        if not resp["Volumes"]:
            logger.error("Volume %s not found in region %s.", volume_id, REGION)
            sys.exit(1)
        return resp["Volumes"][0]
    except ClientError as e:
        logger.error("Error describing volume %s: %s", volume_id, e)
        sys.exit(1)


def describe_address(ec2_client, allocation_id: str) -> dict:
    """Return the describe_addresses response for a single allocation; exit on error."""
    try:
        resp = ec2_client.describe_addresses(AllocationIds=[allocation_id])
        if not resp["Addresses"]:
            logger.error("Allocation %s not found in region %s.", allocation_id, REGION)
            sys.exit(1)
        return resp["Addresses"][0]
    except ClientError as e:
        logger.error("Error describing address %s: %s", allocation_id, e)
        sys.exit(1)


def run_import_changeset(cf_client, stack_name: str, template_body: str,
                         parameters: list, resources_to_import: list,
                         dry_run: bool) -> None:
    """Create and execute a IMPORT changeset; wait for completion."""
    changeset_name = f"import-{int(time.time())}"
    logger.info("Creating IMPORT changeset %s for stack %s...", changeset_name, stack_name)

    if dry_run:
        logger.info("[DRY RUN] Would create changeset and execute IMPORT — skipping.")
        return

    cf_client.create_change_set(
        StackName=stack_name,
        TemplateBody=template_body,
        Parameters=parameters,
        ChangeSetName=changeset_name,
        ChangeSetType="IMPORT",
        ResourcesToImport=resources_to_import,
    )

    # Wait for changeset to reach a state where it can be executed
    logger.info("Waiting for changeset to be ready...")
    while True:
        cs = cf_client.describe_change_set(
            StackName=stack_name,
            ChangeSetName=changeset_name,
        )
        status = cs["Status"]
        if status == "CREATE_COMPLETE":
            break
        if status in ("FAILED", "DELETE_COMPLETE"):
            reason = cs.get("StatusReason", "no reason given")
            logger.error("Changeset %s failed: %s", changeset_name, reason)
            sys.exit(1)
        time.sleep(3)

    logger.info("Executing IMPORT changeset...")
    cf_client.execute_change_set(
        StackName=stack_name,
        ChangeSetName=changeset_name,
    )

    # Poll for IMPORT_COMPLETE — no boto3 waiter exists for this state
    logger.info("Waiting for IMPORT_COMPLETE (this may take a minute)...")
    while True:
        desc = cf_client.describe_stacks(StackName=stack_name)
        status = desc["Stacks"][0]["StackStatus"]
        if status == "IMPORT_COMPLETE":
            logger.info("Stack %s imported successfully.", stack_name)
            return
        if "FAILED" in status or "ROLLBACK" in status:
            logger.error("Import failed with status: %s", status)
            sys.exit(1)
        time.sleep(5)


def update_stack(cf_client, stack_name: str, template_body: str, parameters: list) -> None:
    """Update an existing stack with a new template; wait for UPDATE_COMPLETE.

    Used as the final step of the mixed import flow to add outputs that belong to
    newly-imported resources — CFN forbids adding outputs inside an IMPORT changeset,
    so they must be introduced via a regular update after the import completes.
    """
    logger.info("Updating stack %s to add outputs for imported resources...", stack_name)
    cf_client.update_stack(
        StackName=stack_name,
        TemplateBody=template_body,
        Parameters=parameters,
    )
    waiter = cf_client.get_waiter("stack_update_complete")
    try:
        waiter.wait(StackName=stack_name)
        logger.info("Stack %s updated successfully.", stack_name)
    except WaiterError as e:
        logger.error("Stack update failed: %s", e)
        sys.exit(1)


def create_stack(cf_client, stack_name: str, template_body: str, parameters: list,
                 dry_run: bool) -> None:
    """Create a new persistent stack; wait for CREATE_COMPLETE."""
    logger.info("Creating stack %s...", stack_name)
    if dry_run:
        logger.info("[DRY RUN] Would call create_stack('%s') — skipping.", stack_name)
        return

    cf_client.create_stack(
        StackName=stack_name,
        TemplateBody=template_body,
        Parameters=parameters,
        OnFailure="DELETE",
    )
    logger.info("Creation initiated. Waiting for CREATE_COMPLETE...")
    waiter = cf_client.get_waiter("stack_create_complete")
    try:
        waiter.wait(StackName=stack_name)
        logger.info("Stack %s created successfully.", stack_name)
    except WaiterError as e:
        logger.error("Stack creation failed: %s", e)
        sys.exit(1)


def main():
    env_stack_name = os.getenv("GAME_PERSISTENT_STACK", DEFAULT_STACK_NAME)

    parser = argparse.ArgumentParser(
        description="Create or import the GamePersistentStack (EBS volume + Elastic IP)"
    )
    parser.add_argument(
        "--import-volume-id",
        help="VolumeId of an existing EBS volume to adopt into the stack",
    )
    parser.add_argument(
        "--import-allocation-id",
        help="AllocationId of an existing Elastic IP to adopt into the stack",
    )
    parser.add_argument(
        "--availability-zone",
        help=(
            "Availability Zone for new resources. Required in create mode and when "
            "importing an EIP without a volume (AZ derived from volume otherwise)."
        ),
    )
    parser.add_argument(
        "--volume-size",
        type=int,
        default=10,
        help="EBS volume size in GiB (default: 10; ignored in import mode — actual size used)",
    )
    parser.add_argument(
        "--volume-type",
        default="gp3",
        help="EBS volume type (default: gp3; ignored in import mode — actual type used)",
    )
    parser.add_argument(
        "--persistent-stack",
        default=env_stack_name,
        help=f"Stack name override (default: GAME_PERSISTENT_STACK env or '{DEFAULT_STACK_NAME}')",
    )
    parser.add_argument(
        "--profile",
        default="default",
        help="AWS profile name (default: default)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform AWS state changes. Default is a dry run — safe by default.",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        dest="yes",
        help="Skip interactive confirmations when running with --execute",
    )
    args = parser.parse_args()

    # Dry run: no prompts needed (nothing destructive to confirm)
    if not args.execute:
        args.yes = True

    cf_client = get_cf_client(args.profile)
    ec2_client = get_ec2_client(args.profile)

    stack_name = args.persistent_stack

    # Guard: refuse to run if the stack already exists
    if stack_exists(cf_client, stack_name):
        logger.error(
            "Stack %s already exists. Use 'aws cloudformation update-stack' to modify it "
            "(e.g. to resize the volume).",
            stack_name,
        )
        sys.exit(1)

    importing_volume = args.import_volume_id is not None
    importing_eip = args.import_allocation_id is not None
    import_mode = importing_volume or importing_eip

    # Resolve parameters and AZ
    if importing_volume:
        vol = describe_volume(ec2_client, args.import_volume_id)
        volume_size = vol["Size"]
        volume_type = vol["VolumeType"]
        availability_zone = vol["AvailabilityZone"]
        logger.info(
            "Imported volume %s: size=%dGiB type=%s az=%s",
            args.import_volume_id, volume_size, volume_type, availability_zone,
        )
        # CLI --availability-zone must match volume's actual AZ if provided
        if args.availability_zone and args.availability_zone != availability_zone:
            logger.error(
                "--availability-zone %s does not match volume's actual AZ %s.",
                args.availability_zone, availability_zone,
            )
            sys.exit(1)
    else:
        # Create new volume: use CLI-provided values
        volume_size = args.volume_size
        volume_type = args.volume_type
        if not args.availability_zone:
            logger.error("--availability-zone is required when not importing a volume.")
            sys.exit(1)
        availability_zone = args.availability_zone

    if importing_eip:
        addr = describe_address(ec2_client, args.import_allocation_id)
        logger.info(
            "Imported EIP %s: PublicIp=%s",
            args.import_allocation_id, addr.get("PublicIp", "unknown"),
        )

    parameters = [
        {"ParameterKey": "VolumeSize", "ParameterValue": str(volume_size)},
        {"ParameterKey": "VolumeType", "ParameterValue": volume_type},
        {"ParameterKey": "AvailabilityZone", "ParameterValue": availability_zone},
    ]

    # Summary — always emitted regardless of dry-run or --yes
    logger.info("Stack: %s", stack_name)
    logger.info("Mode:  %s", "IMPORT" if import_mode else "CREATE")
    logger.info("Parameters:")
    logger.info("  VolumeSize:       %s GiB", volume_size)
    logger.info("  VolumeType:       %s", volume_type)
    logger.info("  AvailabilityZone: %s", availability_zone)
    if importing_volume:
        logger.info("  ImportVolumeId:      %s", args.import_volume_id)
    if importing_eip:
        logger.info("  ImportAllocationId:  %s", args.import_allocation_id)

    if not args.execute:
        result = {
            "dry_run": True,
            "stack_name": stack_name,
            "mode": "IMPORT" if import_mode else "CREATE",
            "parameters": {p["ParameterKey"]: p["ParameterValue"] for p in parameters},
        }
        if importing_volume:
            result["import_volume_id"] = args.import_volume_id
        if importing_eip:
            result["import_allocation_id"] = args.import_allocation_id
        print(yaml.dump(result, default_flow_style=False, sort_keys=False))
        return

    if not args.yes:
        confirm = input("\nProceed? Press Enter to continue or Ctrl+C to abort: ")
        if confirm != "":
            print("Aborted.")
            sys.exit(0)

    template_body = read_template()

    if import_mode:
        resources_to_import = []
        if importing_volume:
            resources_to_import.append({
                "ResourceType": "AWS::EC2::Volume",
                "LogicalResourceId": "PersistentVolume",
                "ResourceIdentifier": {"VolumeId": args.import_volume_id},
            })
        if importing_eip:
            resources_to_import.append({
                "ResourceType": "AWS::EC2::EIP",
                "LogicalResourceId": "PersistentEIP",
                "ResourceIdentifier": {"AllocationId": args.import_allocation_id},
            })

        imported_ids = {r["LogicalResourceId"] for r in resources_to_import}
        assert imported_ids, "when import_mode is true, fill at least one resource to imported_ids"
        all_resource_ids = set(yaml.load(template_body, Loader=_CfnLoader).get("Resources", {}).keys())
        fresh_ids = all_resource_ids - imported_ids

        if fresh_ids:
            # Mixed case: some resources imported, others created fresh.
            # CFN IMPORT on a new stack requires ALL resources in ResourcesToImport —
            # fresh resources cannot be mixed in. Three-step workaround:
            # Step 1: create_stack with only the fresh resources (partial template).
            # Step 2: IMPORT changeset — all resources, but only pre-existing outputs.
            #         CFN forbids adding outputs in an IMPORT changeset, so the imported
            #         resources' outputs are withheld here and added in step 3.
            # Step 3: Regular update with the full template to add the withheld outputs.
            logger.info(
                "Mixed import: creating %s fresh, then importing %s...",
                sorted(fresh_ids), sorted(imported_ids),
            )
            partial_template = build_partial_template(template_body, fresh_ids)
            create_stack(cf_client, stack_name, partial_template, parameters, dry_run=False)

            # Build import template: all resources, but only fresh-resource outputs.
            import_template = build_partial_template(
                template_body, all_resource_ids, output_resource_ids=fresh_ids,
            )
            run_import_changeset(
                cf_client, stack_name, import_template, parameters,
                resources_to_import, dry_run=False,
            )

            # Add outputs for imported resources via a regular update.
            update_stack(cf_client, stack_name, template_body, parameters)
        else:
            # All resources imported in a single IMPORT changeset on a new stack.
            run_import_changeset(
                cf_client, stack_name, template_body, parameters,
                resources_to_import, dry_run=False,
            )
    else:
        create_stack(cf_client, stack_name, template_body, parameters, dry_run=False)

    outputs = get_stack_outputs(cf_client, stack_name)
    result = {
        "stack_name": stack_name,
        "outputs": outputs,
    }
    print(yaml.dump(result, default_flow_style=False, sort_keys=False))

    if "PublicIp" in outputs:
        logger.info("Elastic IP: %s — update saved server addresses to this value.", outputs["PublicIp"])


if __name__ == "__main__":
    main()
