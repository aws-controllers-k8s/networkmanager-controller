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

"""Integration tests for the CoreNetworkPolicy API.
"""

import json
import pytest
import time
import logging

from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from e2e import service_marker
from e2e.tests.helper import NetworkManagerValidator
from e2e import CRD_GROUP, CRD_VERSION, load_networkmanager_resource
from e2e.replacement_values import REPLACEMENT_VALUES


CORE_NETWORK_POLICY_RESOURCE_PLURAL = "corenetworkpolicies"
CORE_NETWORK_RESOURCE_PLURAL = "corenetworks"
CREATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10
MODIFY_WAIT_AFTER_SECONDS = 15
# CoreNetwork + Policy operations are async and can take minutes
POLICY_SYNC_WAIT_PERIODS = 30

MINIMAL_POLICY_DOCUMENT = json.dumps({
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

UPDATED_POLICY_DOCUMENT = json.dumps({
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
            "description": "Updated shared segment",
            "require-attachment-acceptance": False
        },
        {
            "name": "production",
            "description": "Production segment",
            "require-attachment-acceptance": True
        }
    ]
})


@pytest.fixture
def core_network_for_policy(networkmanager_client):
    """Create a GlobalNetwork + CoreNetwork via boto3 as prerequisites for
    CoreNetworkPolicy tests. Returns (global_network_id, core_network_id)."""
    validator = NetworkManagerValidator(networkmanager_client)

    # Create GlobalNetwork
    gn_id = validator.create_global_network(description="e2e policy test prerequisite")

    # Create CoreNetwork via boto3
    res = networkmanager_client.create_core_network(
        GlobalNetworkId=gn_id,
        Description="e2e policy test core network",
    )
    cn_id = res["CoreNetwork"]["CoreNetworkId"]

    # Wait for CoreNetwork to become AVAILABLE
    validator.wait_for_core_network_state(cn_id, "AVAILABLE", max_wait_secs=300)

    yield (gn_id, cn_id)

    # Cleanup: delete CoreNetwork then GlobalNetwork
    try:
        networkmanager_client.delete_core_network(CoreNetworkId=cn_id)
        time.sleep(30)
    except Exception:
        pass
    validator.delete_global_network(gn_id)


@pytest.fixture
def simple_core_network_policy(request, networkmanager_client, core_network_for_policy):
    (_, core_network_id) = core_network_for_policy
    resource_name = random_suffix_name("cn-policy-ack-test", 31)
    replacements = REPLACEMENT_VALUES.copy()
    replacements["CORE_NETWORK_POLICY_NAME"] = resource_name
    replacements["CORE_NETWORK_ID"] = core_network_id
    replacements["POLICY_DOCUMENT"] = MINIMAL_POLICY_DOCUMENT
    replacements["AUTO_EXECUTE"] = "true"

    marker = request.node.get_closest_marker("resource_data")
    if marker is not None:
        data = marker.args[0]
        if 'policy_document' in data:
            replacements["POLICY_DOCUMENT"] = data['policy_document']
        if 'auto_execute' in data:
            replacements["AUTO_EXECUTE"] = data['auto_execute']

    resource_data = load_networkmanager_resource(
        "core_network_policy",
        additional_replacements=replacements,
    )
    logging.debug(resource_data)

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, CORE_NETWORK_POLICY_RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    time.sleep(CREATE_WAIT_AFTER_SECONDS)

    cr = k8s.wait_resource_consumed_by_controller(ref)
    assert cr is not None
    assert k8s.get_resource_exists(ref)

    yield (ref, cr, core_network_id)

    try:
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted
    except:
        pass


@service_marker
@pytest.mark.canary
class TestCoreNetworkPolicy:
    def test_create_with_auto_execute(self, networkmanager_client, simple_core_network_policy):
        """Test creating a CoreNetworkPolicy with autoExecute=true.
        The change set should be automatically executed."""
        (ref, cr, core_network_id) = simple_core_network_policy

        # Wait for the policy to be synced (change set executed)
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=POLICY_SYNC_WAIT_PERIODS)

        # Verify the policy exists in AWS
        validator = NetworkManagerValidator(networkmanager_client)
        policy = validator.get_core_network_policy(core_network_id)
        assert policy is not None

        # Verify status fields
        resource = k8s.get_resource(ref)
        assert resource["status"].get("policyVersionID") is not None
        assert resource["status"].get("changeSetState") in ["EXECUTION_SUCCEEDED", "EXECUTED"]

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref, 2, 5)
        assert deleted is True

    def test_update_policy(self, networkmanager_client, simple_core_network_policy):
        """Test updating the policy document triggers a new change set."""
        (ref, cr, core_network_id) = simple_core_network_policy

        # Wait for initial policy to be synced
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=POLICY_SYNC_WAIT_PERIODS)

        # Get the initial policy version
        resource = k8s.get_resource(ref)
        initial_version = resource["status"].get("policyVersionID")

        # Update the policy document
        updates = {
            "spec": {"policyDocument": UPDATED_POLICY_DOCUMENT}
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        # Wait for updated policy to be synced
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=POLICY_SYNC_WAIT_PERIODS)

        # Verify the policy version changed
        resource = k8s.get_resource(ref)
        updated_version = resource["status"].get("policyVersionID")
        assert updated_version is not None
        assert updated_version != initial_version

        # Verify the updated policy in AWS
        validator = NetworkManagerValidator(networkmanager_client)
        policy = validator.get_core_network_policy(core_network_id)
        assert policy is not None
        policy_doc = json.loads(policy["PolicyDocument"])
        assert len(policy_doc["segments"]) == 2

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref, 2, 5)
        assert deleted is True

    @pytest.mark.resource_data({'auto_execute': 'false'})
    def test_create_without_auto_execute(self, networkmanager_client, simple_core_network_policy):
        """Test creating a CoreNetworkPolicy with autoExecute=false.
        The change set should remain in READY_TO_EXECUTE state."""
        (ref, cr, core_network_id) = simple_core_network_policy

        # Give the controller time to process
        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        # The policy should NOT be synced (waiting for manual execution)
        resource = k8s.get_resource(ref)
        change_set_state = resource["status"].get("changeSetState")
        # It should be in READY_TO_EXECUTE or still processing
        assert change_set_state in ["READY_TO_EXECUTE", None, ""]

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref, 2, 5)
        assert deleted is True
