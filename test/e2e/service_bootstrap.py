# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
# 	 http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""Bootstraps resources required by Network Manager integration tests."""

import json
import logging
import time
import uuid
from collections.abc import Callable

import boto3
from botocore.exceptions import ClientError

from e2e import bootstrap_directory
from e2e.bootstrap_resources import (
    NETWORKMANAGER_CONTROL_PLANE_REGION,
    BootstrapResources,
)
from e2e.service_cleanup import cleanup_resources

VPC_CIDR = "10.99.0.0/16"
SUBNET_CIDRS = ("10.99.1.0/24", "10.99.2.0/24")
BOOTSTRAP_TIMEOUT_SECONDS = 1800
POLL_INTERVAL_SECONDS = 15
TEST_TAG_KEY = "ack-test-resource"
TEST_TAG_VALUE = "networkmanager-vpc-attachment"

LOGGER = logging.getLogger(__name__)


def _wait_until(
    predicate: Callable[[], bool],
    description: str,
    timeout_seconds: int = BOOTSTRAP_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Timed out waiting for {description}")


def _core_network_policy(region: str) -> str:
    policy = {
        "version": "2021.12",
        "core-network-configuration": {
            "asn-ranges": ["64512-65534"],
            "edge-locations": [{"location": region}],
        },
        "segments": [
            {
                "name": "acktest",
                "require-attachment-acceptance": False,
            }
        ],
        "attachment-policies": [
            {
                "rule-number": 100,
                "condition-logic": "and",
                "conditions": [{"type": "any"}],
                "action": {
                    "association-method": "constant",
                    "segment": "acktest",
                },
            }
        ],
    }
    return json.dumps(policy, separators=(",", ":"))


def _wait_for_global_network(networkmanager, global_network_id: str) -> None:
    def available() -> bool:
        response = networkmanager.describe_global_networks(
            GlobalNetworkIds=[global_network_id],
        )
        networks = response.get("GlobalNetworks", [])
        return bool(networks and networks[0].get("State") == "AVAILABLE")

    _wait_until(
        available,
        f"Global Network {global_network_id} to become AVAILABLE",
    )


def _wait_for_core_network(networkmanager, core_network_id: str) -> None:
    def available_with_live_policy() -> bool:
        try:
            response = networkmanager.get_core_network(
                CoreNetworkId=core_network_id,
            )
            if response.get("CoreNetwork", {}).get("State") != "AVAILABLE":
                return False

            latest_policy = networkmanager.get_core_network_policy(
                CoreNetworkId=core_network_id,
                Alias="LATEST",
            ).get("CoreNetworkPolicy", {})
            if latest_policy.get("ChangeSetState") == "FAILED_GENERATION":
                raise RuntimeError(
                    f"Core Network policy generation failed: "
                    f"{latest_policy.get('PolicyErrors', [])}"
                )

            live_policy = networkmanager.get_core_network_policy(
                CoreNetworkId=core_network_id,
                Alias="LIVE",
            ).get("CoreNetworkPolicy", {})
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == (
                "ResourceNotFoundException"
            ):
                return False
            raise

        return live_policy.get("ChangeSetState") == "EXECUTION_SUCCEEDED"

    _wait_until(
        available_with_live_policy,
        f"Core Network {core_network_id} and its policy to become available",
    )


def service_bootstrap() -> BootstrapResources:
    LOGGER.setLevel(logging.INFO)

    ec2 = boto3.client("ec2")
    networkmanager = boto3.client(
        "networkmanager",
        region_name=NETWORKMANAGER_CONTROL_PLANE_REGION,
    )
    sts = boto3.client("sts")

    account_id = sts.get_caller_identity()["Account"]
    region = ec2.meta.region_name
    run_name = f"ack-networkmanager-{uuid.uuid4().hex[:8]}"
    tags = [
        {"Key": "Name", "Value": run_name},
        {"Key": TEST_TAG_KEY, "Value": TEST_TAG_VALUE},
    ]
    resources = BootstrapResources()

    try:
        LOGGER.info("Creating Global Network for VpcAttachment tests...")
        response = networkmanager.create_global_network(
            Description=run_name,
            Tags=tags,
        )
        resources.GlobalNetworkId = response["GlobalNetwork"]["GlobalNetworkId"]
        _wait_for_global_network(networkmanager, resources.GlobalNetworkId)
        LOGGER.info("Created Global Network: %s", resources.GlobalNetworkId)

        LOGGER.info("Creating Core Network and auto-accept policy...")
        response = networkmanager.create_core_network(
            GlobalNetworkId=resources.GlobalNetworkId,
            Description=run_name,
            Tags=tags,
            PolicyDocument=_core_network_policy(region),
        )
        resources.CoreNetworkId = response["CoreNetwork"]["CoreNetworkId"]
        _wait_for_core_network(networkmanager, resources.CoreNetworkId)
        LOGGER.info("Created Core Network: %s", resources.CoreNetworkId)

        LOGGER.info("Creating VPC for VpcAttachment tests...")
        response = ec2.create_vpc(
            CidrBlock=VPC_CIDR,
            TagSpecifications=[
                {"ResourceType": "vpc", "Tags": tags},
            ],
        )
        resources.VpcId = response["Vpc"]["VpcId"]
        resources.VpcArn = f"arn:aws:ec2:{region}:{account_id}:vpc/{resources.VpcId}"
        ec2.get_waiter("vpc_available").wait(VpcIds=[resources.VpcId])
        LOGGER.info("Created VPC: %s", resources.VpcId)

        availability_zones = ec2.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]}]
        )["AvailabilityZones"]
        if len(availability_zones) < len(SUBNET_CIDRS):
            raise RuntimeError(
                f"Region {region} requires at least two available AZs for "
                "VpcAttachment e2e tests"
            )

        subnet_fields = (
            ("SubnetId1", "SubnetArn1"),
            ("SubnetId2", "SubnetArn2"),
        )
        for cidr, availability_zone, fields in zip(
            SUBNET_CIDRS,
            availability_zones,
            subnet_fields,
        ):
            response = ec2.create_subnet(
                VpcId=resources.VpcId,
                CidrBlock=cidr,
                AvailabilityZone=availability_zone["ZoneName"],
                TagSpecifications=[
                    {"ResourceType": "subnet", "Tags": tags},
                ],
            )
            subnet_id = response["Subnet"]["SubnetId"]
            subnet_arn = f"arn:aws:ec2:{region}:{account_id}:subnet/{subnet_id}"
            setattr(resources, fields[0], subnet_id)
            setattr(resources, fields[1], subnet_arn)
            LOGGER.info("Created subnet: %s", subnet_id)

        resources.serialize(bootstrap_directory)
        return resources
    except Exception:
        LOGGER.exception("Network Manager test bootstrap failed; cleaning up")
        try:
            cleanup_resources(resources)
        except Exception:
            LOGGER.exception("Cleanup after bootstrap failure also failed")
        raise


if __name__ == "__main__":
    service_bootstrap()
