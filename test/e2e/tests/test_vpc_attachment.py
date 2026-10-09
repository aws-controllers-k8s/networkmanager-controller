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

"""Integration tests for the VPCAttachment API.

The Global Network, Core Network and auto-accept policy, VPC, and subnets are
created by service_bootstrap.py and removed by service_cleanup.py. The ACK
controller under test owns only the VPC attachment custom resource.
"""

import logging
import time

import pytest
from acktest import tags
from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name

from e2e import CRD_GROUP, CRD_VERSION, load_networkmanager_resource, service_marker
from e2e.bootstrap_resources import get_bootstrap_resources
from e2e.replacement_values import REPLACEMENT_VALUES
from e2e.tests.helper import NetworkManagerValidator

LOGGER = logging.getLogger(__name__)


VPC_ATTACHMENT_RESOURCE_PLURAL = "vpcattachments"
CREATE_WAIT_AFTER_SECONDS = 15
DELETE_WAIT_PERIODS = 180
DELETE_POLL_SECONDS = 10
MODIFY_WAIT_AFTER_SECONDS = 20
TAG_WAIT_PERIODS = 120
TAG_POLL_SECONDS = 5


def wait_for_attachment_tags(
    networkmanager_client,
    attachment_id,
    expected,
    absent=(),
):
    for _ in range(TAG_WAIT_PERIODS):
        attachment = networkmanager_client.get_vpc_attachment(
            AttachmentId=attachment_id,
        )["VpcAttachment"]["Attachment"]
        actual = {tag["Key"]: tag["Value"] for tag in attachment.get("Tags", [])}
        if all(actual.get(key) == value for key, value in expected.items()) and all(
            key not in actual for key in absent
        ):
            return attachment["Tags"]
        time.sleep(TAG_POLL_SECONDS)
    raise AssertionError(
        f"Timed out waiting for attachment tags: expected={expected}, absent={absent}"
    )


@pytest.fixture
def simple_vpc_attachment(networkmanager_client):
    resources = get_bootstrap_resources()
    resource_name = random_suffix_name("vpc-attachment-ack-test", 31)

    replacements = REPLACEMENT_VALUES.copy()
    replacements["VPC_ATTACHMENT_NAME"] = resource_name
    replacements["CORE_NETWORK_ID"] = resources.CoreNetworkId
    replacements["VPC_ARN"] = resources.VpcArn
    replacements["SUBNET_ARN_1"] = resources.SubnetArn1
    replacements["TAG_KEY"] = "initialtagkey"
    replacements["TAG_VALUE"] = "initialtagvalue"

    resource_data = load_networkmanager_resource(
        "vpc_attachment",
        additional_replacements=replacements,
    )
    LOGGER.debug(resource_data)

    ref = k8s.CustomResourceReference(
        CRD_GROUP,
        CRD_VERSION,
        VPC_ATTACHMENT_RESOURCE_PLURAL,
        resource_name,
        namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    time.sleep(CREATE_WAIT_AFTER_SECONDS)

    cr = k8s.wait_resource_consumed_by_controller(ref)
    assert cr is not None
    assert k8s.get_resource_exists(ref)

    # Prow runs tests across multiple pytest-xdist workers. Keep all
    # VpcAttachment lifecycle assertions in one test (below), and do not
    # expose the fixture until AWS has returned the generated attachment ID.
    # A status object alone only means the controller has consumed the CR; it
    # does not guarantee CreateVpcAttachment has completed.
    assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)
    cr = k8s.get_resource(ref)
    assert cr.get("status", {}).get("attachmentID")

    yield (ref, cr)

    attachment_id = cr.get("status", {}).get("attachmentID")
    if k8s.get_resource_exists(ref):
        _, deleted = k8s.delete_custom_resource(
            ref,
            DELETE_WAIT_PERIODS,
            DELETE_POLL_SECONDS,
        )
        assert deleted
    if attachment_id:
        NetworkManagerValidator(networkmanager_client).assert_vpc_attachment(
            attachment_id,
            exists=False,
        )


@service_marker
@pytest.mark.canary
class TestVPCAttachment:
    @pytest.mark.resource_data(
        {"tag_key": "initialtagkey", "tag_value": "initialtagvalue"}
    )
    def test_crud(self, networkmanager_client, simple_vpc_attachment):
        """Exercises the complete VpcAttachment lifecycle sequentially.

        Network Manager permits a VPC to have only one Core Network attachment.
        Prow distributes individual test methods across pytest-xdist workers,
        and class-scoped fixtures are not shared between worker processes.
        Keeping create/read/options, tags, and subnet updates in one test avoids
        concurrent attempts to attach the same bootstrapped VPC while retaining
        coverage for every supported mutable field.
        """
        (ref, cr) = simple_vpc_attachment
        resources = get_bootstrap_resources()
        networkmanager_validator = NetworkManagerValidator(networkmanager_client)

        # Attachment ID is populated by the syncTopLevelStatus hook; without
        # it, requiredFieldsMissingFromReadOneInput would stay true forever
        # and the controller would never progress past Create.
        attachment_id = cr["status"]["attachmentID"]

        networkmanager_validator.assert_vpc_attachment(attachment_id)
        vpc_attachment = networkmanager_validator.get_vpc_attachment(attachment_id)
        assert vpc_attachment["Attachment"]["State"] == "AVAILABLE"
        assert vpc_attachment["Attachment"]["CoreNetworkId"] == resources.CoreNetworkId
        assert resources.SubnetArn1 in vpc_attachment["SubnetArns"]
        assert vpc_attachment["Options"]["ApplianceModeSupport"] is False
        assert vpc_attachment["Options"]["DnsSupport"] is True
        assert vpc_attachment["Options"]["Ipv6Support"] is False
        assert vpc_attachment["Options"]["SecurityGroupReferencingSupport"] is True

        tags.assert_ack_system_tags(tags=vpc_attachment["Attachment"]["Tags"])
        tags.assert_equal_without_ack_tags(
            expected={"initialtagkey": "initialtagvalue"},
            actual=vpc_attachment["Attachment"]["Tags"],
        )

        # Update tags only. This must route through TagResource/UntagResource
        # (the sdk_update_pre_build_request hook) and must NOT call
        # UpdateVpcAttachment, since that API has no Tags field.
        updates = {
            "spec": {"tags": [{"key": "updatedtagkey", "value": "updatedtagvalue"}]},
        }
        k8s.patch_custom_resource(ref, updates)
        updated_tags = wait_for_attachment_tags(
            networkmanager_client,
            attachment_id,
            expected={"updatedtagkey": "updatedtagvalue"},
            absent=("initialtagkey",),
        )
        tags.assert_ack_system_tags(tags=updated_tags)
        tags.assert_equal_without_ack_tags(
            expected={"updatedtagkey": "updatedtagvalue"},
            actual=updated_tags,
        )

        # Remove all user tags; system tags must persist.
        k8s.patch_custom_resource(ref, {"spec": {"tags": []}})
        remaining_tags = wait_for_attachment_tags(
            networkmanager_client,
            attachment_id,
            expected={},
            absent=("initialtagkey", "updatedtagkey"),
        )
        tags.assert_ack_system_tags(tags=remaining_tags)
        tags.assert_equal_without_ack_tags(
            expected={},
            actual=remaining_tags,
        )

        # UpdateVpcAttachment models subnet membership as exact additions and
        # removals. Adding SubnetArn2 must preserve SubnetArn1 rather than
        # treating the desired list as a blind replacement or re-addition.
        updates = {
            "spec": {"subnetARNs": [resources.SubnetArn1, resources.SubnetArn2]},
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

        vpc_attachment = networkmanager_validator.get_vpc_attachment(attachment_id)
        assert resources.SubnetArn1 in vpc_attachment["SubnetArns"]
        assert resources.SubnetArn2 in vpc_attachment["SubnetArns"]
        assert len(vpc_attachment["SubnetArns"]) == 2
