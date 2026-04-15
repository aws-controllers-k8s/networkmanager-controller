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

"""Integration tests for the VpcAttachment API.

These tests require VPC infrastructure (VPC + subnets) and a CoreNetwork
with a policy that has attachment acceptance disabled. Run with --runslow.
"""

import json
import pytest
import time
import logging

from acktest import tags
from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from e2e import service_marker
from e2e.tests.helper import NetworkManagerValidator
from e2e import CRD_GROUP, CRD_VERSION, load_networkmanager_resource
from e2e.replacement_values import REPLACEMENT_VALUES
from e2e.bootstrap_resources import get_bootstrap_resources


VPC_ATTACHMENT_RESOURCE_PLURAL = "vpcattachments"
CREATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 30
MODIFY_WAIT_AFTER_SECONDS = 15
ATTACHMENT_SYNC_WAIT_PERIODS = 30


@pytest.fixture(scope="module")
def cloud_wan_infrastructure(networkmanager_client):
    """Create GlobalNetwork + CoreNetwork + Policy with attachment acceptance
    disabled. This is required for VpcAttachment tests.
    Returns (global_network_id, core_network_id)."""
    validator = NetworkManagerValidator(networkmanager_client)
    gn_id = validator.create_global_network(description="e2e vpc attachment test")

    res = networkmanager_client.create_core_network(
        GlobalNetworkId=gn_id,
        Description="e2e vpc attachment test core network",
    )
    cn_id = res["CoreNetwork"]["CoreNetworkId"]
    validator.wait_for_core_network_state(cn_id, "AVAILABLE", max_wait_secs=300)

    # Apply a policy with attachment acceptance disabled
    policy = json.dumps({
        "version": "2021.12",
        "core-network-configuration": {
            "vpn-ecmp-support": False,
            "asn-ranges": ["64512-65534"],
            "edge-locations": [
                {"location": "us-east-1"}
            ]
        },
        "segments": [
            {
                "name": "shared",
                "description": "Shared segment",
                "require-attachment-acceptance": False
            }
        ]
    })
    networkmanager_client.put_core_network_policy(
        CoreNetworkId=cn_id,
        PolicyDocument=policy,
    )
    # Wait for policy to be ready then execute
    time.sleep(10)
    try:
        policy_resp = networkmanager_client.get_core_network_policy(CoreNetworkId=cn_id)
        pv = policy_resp["CoreNetworkPolicy"]["PolicyVersionId"]
        networkmanager_client.execute_core_network_change_set(
            CoreNetworkId=cn_id,
            PolicyVersionId=pv,
        )
    except Exception as e:
        logging.warning(f"Could not auto-execute policy change set: {e}")

    # Wait for core network to settle
    validator.wait_for_core_network_state(cn_id, "AVAILABLE", max_wait_secs=300)

    yield (gn_id, cn_id)

    try:
        networkmanager_client.delete_core_network(CoreNetworkId=cn_id)
        time.sleep(30)
    except Exception:
        pass
    validator.delete_global_network(gn_id)


@pytest.fixture
def simple_vpc_attachment(request, networkmanager_client, cloud_wan_infrastructure):
    bootstrap_resources = get_bootstrap_resources()
    (_, core_network_id) = cloud_wan_infrastructure

    resource_name = random_suffix_name("vpc-attach-ack-test", 31)
    replacements = REPLACEMENT_VALUES.copy()
    replacements["VPC_ATTACHMENT_NAME"] = resource_name
    replacements["CORE_NETWORK_ID"] = core_network_id
    replacements["VPC_ARN"] = bootstrap_resources.VpcArn
    replacements["SUBNET_ARN_1"] = bootstrap_resources.SubnetArn1
    replacements["SUBNET_ARN_2"] = bootstrap_resources.SubnetArn2

    marker = request.node.get_closest_marker("resource_data")
    if marker is not None:
        data = marker.args[0]
        if 'tag_key' in data:
            replacements["TAG_KEY"] = data['tag_key']
        if 'tag_value' in data:
            replacements["TAG_VALUE"] = data['tag_value']

    resource_data = load_networkmanager_resource(
        "vpc_attachment",
        additional_replacements=replacements,
    )
    logging.debug(resource_data)

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, VPC_ATTACHMENT_RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    time.sleep(CREATE_WAIT_AFTER_SECONDS)

    cr = k8s.wait_resource_consumed_by_controller(ref)
    assert cr is not None
    assert k8s.get_resource_exists(ref)

    yield (ref, cr)

    try:
        _, deleted = k8s.delete_custom_resource(ref, 3, 30)
        assert deleted
    except:
        pass


@service_marker
@pytest.mark.slow
class TestVpcAttachment:
    @pytest.mark.resource_data({'tag_key': 'initialtagkey', 'tag_value': 'initialtagvalue'})
    def test_crud(self, networkmanager_client, simple_vpc_attachment):
        (ref, cr) = simple_vpc_attachment

        attachment_id = cr["status"]["attachmentID"]
        assert attachment_id is not None

        # Wait for attachment to become AVAILABLE
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=ATTACHMENT_SYNC_WAIT_PERIODS)

        # Check VpcAttachment exists in AWS
        validator = NetworkManagerValidator(networkmanager_client)
        validator.assert_vpc_attachment(attachment_id)

        vpc_attachment = validator.get_vpc_attachment(attachment_id)
        assert vpc_attachment is not None

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref, 2, 30)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check attachment no longer exists
        validator.assert_vpc_attachment(attachment_id, exists=False)

    @pytest.mark.resource_data({'tag_key': 'initialtagkey', 'tag_value': 'initialtagvalue'})
    def test_crud_tags(self, networkmanager_client, simple_vpc_attachment):
        (ref, cr) = simple_vpc_attachment

        attachment_id = cr["status"]["attachmentID"]
        assert attachment_id is not None

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=ATTACHMENT_SYNC_WAIT_PERIODS)

        validator = NetworkManagerValidator(networkmanager_client)
        vpc_attachment = validator.get_vpc_attachment(attachment_id)
        assert vpc_attachment is not None

        # Check tags on the attachment
        attachment_tags = vpc_attachment.get("Attachment", {}).get("Tags", [])
        user_tags = {"initialtagkey": "initialtagvalue"}
        tags.assert_ack_system_tags(tags=attachment_tags)
        tags.assert_equal_without_ack_tags(expected=user_tags, actual=attachment_tags)

        # Update tags
        update_tags = [{"key": "updatedtagkey", "value": "updatedtagvalue"}]
        updates = {"spec": {"tags": update_tags}}
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=ATTACHMENT_SYNC_WAIT_PERIODS)

        vpc_attachment = validator.get_vpc_attachment(attachment_id)
        attachment_tags = vpc_attachment.get("Attachment", {}).get("Tags", [])
        updated_tags = {"updatedtagkey": "updatedtagvalue"}
        tags.assert_ack_system_tags(tags=attachment_tags)
        tags.assert_equal_without_ack_tags(expected=updated_tags, actual=attachment_tags)

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)
        validator.assert_vpc_attachment(attachment_id, exists=False)
