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

"""Cleans up resources created for Network Manager integration tests."""

import logging
import time
from collections.abc import Callable

import boto3
from botocore.exceptions import ClientError

from e2e.bootstrap_resources import (
    NETWORKMANAGER_CONTROL_PLANE_REGION,
    BootstrapResources,
    get_bootstrap_resources,
)

CLEANUP_TIMEOUT_SECONDS = 1800
POLL_INTERVAL_SECONDS = 15

LOGGER = logging.getLogger(__name__)


def _error_code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


def _wait_until(
    predicate: Callable[[], bool],
    description: str,
    timeout_seconds: int = CLEANUP_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Timed out waiting for {description}")


def _test_attachments(networkmanager, resources: BootstrapResources):
    if not resources.CoreNetworkId or not resources.VpcArn:
        return []

    attachments = []
    paginator = networkmanager.get_paginator("list_attachments")
    try:
        pages = paginator.paginate(
            CoreNetworkId=resources.CoreNetworkId,
            AttachmentType="VPC",
        )
        for page in pages:
            for attachment in page.get("Attachments", []):
                if attachment.get("ResourceArn") == resources.VpcArn:
                    attachments.append(attachment)
    except ClientError as error:
        if _error_code(error) == "ResourceNotFoundException":
            return []
        raise
    return attachments


def _delete_test_attachments(networkmanager, resources: BootstrapResources) -> None:
    for attachment in _test_attachments(networkmanager, resources):
        attachment_id = attachment["AttachmentId"]
        if attachment.get("State") != "DELETING":
            LOGGER.info("Deleting VPC attachment: %s", attachment_id)
            try:
                networkmanager.delete_attachment(AttachmentId=attachment_id)
            except ClientError as error:
                if _error_code(error) != "ResourceNotFoundException":
                    raise

    _wait_until(
        lambda: not _test_attachments(networkmanager, resources),
        "test VPC attachments to be deleted",
    )


def _delete_subnet(ec2, subnet_id: str) -> None:
    if not subnet_id:
        return

    LOGGER.info("Deleting subnet: %s", subnet_id)

    def deleted() -> bool:
        try:
            ec2.delete_subnet(SubnetId=subnet_id)
            return True
        except ClientError as error:
            code = _error_code(error)
            if code == "InvalidSubnetID.NotFound":
                return True
            if code == "DependencyViolation":
                return False
            raise

    _wait_until(deleted, f"subnet {subnet_id} to be deleted")


def _delete_vpc(ec2, vpc_id: str) -> None:
    if not vpc_id:
        return

    LOGGER.info("Deleting VPC: %s", vpc_id)

    def deleted() -> bool:
        try:
            ec2.delete_vpc(VpcId=vpc_id)
            return True
        except ClientError as error:
            code = _error_code(error)
            if code == "InvalidVpcID.NotFound":
                return True
            if code == "DependencyViolation":
                return False
            raise

    _wait_until(deleted, f"VPC {vpc_id} to be deleted")


def _delete_core_network(networkmanager, core_network_id: str) -> None:
    if not core_network_id:
        return

    LOGGER.info("Deleting Core Network: %s", core_network_id)

    def deletion_started() -> bool:
        try:
            response = networkmanager.get_core_network(
                CoreNetworkId=core_network_id,
            )
            if response.get("CoreNetwork", {}).get("State") == "DELETING":
                return True
            networkmanager.delete_core_network(CoreNetworkId=core_network_id)
            return True
        except ClientError as error:
            code = _error_code(error)
            if code == "ResourceNotFoundException":
                return True
            if code == "ConflictException":
                return False
            raise

    _wait_until(
        deletion_started,
        f"Core Network {core_network_id} deletion to start",
    )

    def deleted() -> bool:
        try:
            networkmanager.get_core_network(CoreNetworkId=core_network_id)
            return False
        except ClientError as error:
            if _error_code(error) == "ResourceNotFoundException":
                return True
            raise

    _wait_until(deleted, f"Core Network {core_network_id} to be deleted")


def _delete_global_network(networkmanager, global_network_id: str) -> None:
    if not global_network_id:
        return

    LOGGER.info("Deleting Global Network: %s", global_network_id)

    def deletion_started() -> bool:
        try:
            networks = networkmanager.describe_global_networks(
                GlobalNetworkIds=[global_network_id],
            ).get("GlobalNetworks", [])
            if not networks or networks[0].get("State") == "DELETING":
                return True
            networkmanager.delete_global_network(
                GlobalNetworkId=global_network_id,
            )
            return True
        except ClientError as error:
            code = _error_code(error)
            if code == "ResourceNotFoundException":
                return True
            if code == "ConflictException":
                return False
            raise

    _wait_until(
        deletion_started,
        f"Global Network {global_network_id} deletion to start",
    )

    def deleted() -> bool:
        try:
            response = networkmanager.describe_global_networks(
                GlobalNetworkIds=[global_network_id],
            )
            return not response.get("GlobalNetworks", [])
        except ClientError as error:
            if _error_code(error) == "ResourceNotFoundException":
                return True
            raise

    _wait_until(deleted, f"Global Network {global_network_id} to be deleted")


def cleanup_resources(resources: BootstrapResources) -> None:
    """Deletes only resources recorded in this test run's bootstrap state."""
    ec2 = boto3.client("ec2")
    networkmanager = boto3.client(
        "networkmanager",
        region_name=NETWORKMANAGER_CONTROL_PLANE_REGION,
    )

    # The Core Network cannot be deleted while attachments exist, and the VPC
    # cannot be deleted while the attachment's service interfaces still exist.
    _delete_test_attachments(networkmanager, resources)
    _delete_subnet(ec2, resources.SubnetId2)
    _delete_subnet(ec2, resources.SubnetId1)
    _delete_vpc(ec2, resources.VpcId)
    _delete_core_network(networkmanager, resources.CoreNetworkId)
    _delete_global_network(networkmanager, resources.GlobalNetworkId)


def service_cleanup() -> None:
    LOGGER.setLevel(logging.INFO)
    cleanup_resources(get_bootstrap_resources())


if __name__ == "__main__":
    service_cleanup()
