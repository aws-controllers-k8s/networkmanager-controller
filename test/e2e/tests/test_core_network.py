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

"""Integration tests for the CoreNetwork API.
"""

import pytest
import time
import json

from acktest import tags
from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from e2e import service_marker 
from e2e.tests.helper import NetworkManagerValidator
from e2e.tests.test_global_network import simple_global_network
from e2e import CRD_GROUP, CRD_VERSION, load_networkmanager_resource
from e2e.replacement_values import REPLACEMENT_VALUES


DESCRIPTION_DEFAULT = "Test Core Network"
CORE_NETWORK_RESOURCE_PLURAL = "corenetworks"
CORE_NETWORK_POLICY_DEFAULT = json.dumps({
  "version": "2025.11",
  "core-network-configuration": {
    "asn-ranges": [
      "65022-65534"
    ],
    "edge-locations": [
      {
        "location": "us-west-2"
      }
    ]
  },
  "segments": [
    {
      "name": "test"
    }
  ]
})

CREATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 15
MODIFY_WAIT_AFTER_SECONDS = 15


@pytest.fixture
def simple_core_network(request, simple_global_network):
    resource_name = random_suffix_name("core-network-ack-test", 31)
    replacements = REPLACEMENT_VALUES.copy()
    replacements["CORE_NETWORK_NAME"] = resource_name
    replacements["DESCRIPTION"] = DESCRIPTION_DEFAULT
    replacements["POLICY_DOCUMENT"] = ""
    _, global_network_cr = simple_global_network
    global_network_id = global_network_cr["status"]["globalNetworkID"]
    replacements["GLOBAL_NETWORK_ID"] = global_network_id

    marker = request.node.get_closest_marker("resource_data")
    if marker is not None:
        data = marker.args[0]
        if 'description' in data:
            replacements["DESCRIPTION"] = data['description']
        if 'globalNetworkID' in data:
            replacements['GLOBAL_NETWORK_ID'] = data['globalNetworkID']
        if 'policyDocument' in data:
            replacements['POLICY_DOCUMENT'] = data['policyDocument']
        if 'tag_key' in data:
            replacements["TAG_KEY"] = data['tag_key']
        if 'tag_value' in data:
            replacements["TAG_VALUE"] = data['tag_value']

    # Load Core Network CR
    resource_data = load_networkmanager_resource(
        "core_network",
        additional_replacements=replacements,
    )

    # Create k8s resource
    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, CORE_NETWORK_RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    time.sleep(CREATE_WAIT_AFTER_SECONDS)

    cr = k8s.wait_resource_consumed_by_controller(ref)
    assert cr is not None
    assert k8s.get_resource_exists(ref)

    yield (ref, cr)

    try:
        _, deleted = k8s.delete_custom_resource(ref, 6, 10)
        assert deleted
    except:
        pass

@service_marker
@pytest.mark.canary
class TestCoreNetwork:
    @pytest.mark.resource_data({'tag_key': 'initialtagkey', 'tag_value': 'initialtagvalue'})
    def test_crud(self, networkmanager_client, simple_core_network):
        (ref, cr) = simple_core_network
        resource_id = cr["status"]["coreNetworkID"]

        time.sleep(CREATE_WAIT_AFTER_SECONDS)
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

        networkmanager_validator = NetworkManagerValidator(networkmanager_client)
        networkmanager_validator.assert_core_network(resource_id)

        # Validate description
        core_network = networkmanager_validator.get_core_network(resource_id)
        assert core_network["Description"] == DESCRIPTION_DEFAULT

        newDescription = "Updated Description"
        updates = {
            "spec": {"description": newDescription}
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)
        core_network = networkmanager_validator.get_core_network(resource_id)
        assert core_network["Description"] == newDescription

        updates = {
            "spec": {"policyDocument": CORE_NETWORK_POLICY_DEFAULT}
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)
        networkmanager_validator.assert_core_network_segment(resource_id, "test")

        updatedPolicyDocument = json.loads(CORE_NETWORK_POLICY_DEFAULT)
        updatedPolicyDocument["segments"].append({"name": "updated"})
        updates = {
            "spec": {"policyDocument": json.dumps(updatedPolicyDocument)}
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)
        networkmanager_validator.assert_core_network_segment(resource_id, "updated")

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref, 10, 60)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        networkmanager_validator.assert_core_network(resource_id, exists=False)

    @pytest.mark.resource_data({'tag_key': 'initialtagkey', 'tag_value': 'initialtagvalue'})
    def test_crud_tags(self, networkmanager_client, simple_core_network):
        (ref, cr) = simple_core_network
        
        resource = k8s.get_resource(ref)
        resource_id = cr["status"]["coreNetworkID"]

        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        networkmanager_validator = NetworkManagerValidator(networkmanager_client)
        networkmanager_validator.assert_core_network(resource_id)
        
        # Check system and user tags exist for core network resource
        core_network = networkmanager_validator.get_core_network(resource_id)
        user_tags = {
            "initialtagkey": "initialtagvalue"
        }
        tags.assert_ack_system_tags(
            tags=core_network["Tags"],
        )
        tags.assert_equal_without_ack_tags(
            expected=user_tags,
            actual=core_network["Tags"],
        )
        
        # Only user tags should be present in Spec
        assert len(resource["spec"]["tags"]) == 1
        assert resource["spec"]["tags"][0]["key"] == "initialtagkey"
        assert resource["spec"]["tags"][0]["value"] == "initialtagvalue"

        # Update tags
        update_tags = [
                {
                    "key": "updatedtagkey",
                    "value": "updatedtagvalue",
                }
            ]

        # Patch the CoreNetwork, updating the tags with new pair
        updates = {
            "spec": {"tags": update_tags},
        }

        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        # Check resource synced successfully
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)
        
        # Check for updated user tags; system tags should persist
        core_network = networkmanager_validator.get_core_network(resource_id)
       
        updated_tags = {
            "updatedtagkey": "updatedtagvalue"
        }
        tags.assert_ack_system_tags(
            tags=core_network["Tags"],
        )
        tags.assert_equal_without_ack_tags(
            expected=updated_tags,
            actual=core_network["Tags"],
        )
               
        # Only user tags should be present in Spec
        resource = k8s.get_resource(ref)
        assert len(resource["spec"]["tags"]) == 1
        assert resource["spec"]["tags"][0]["key"] == "updatedtagkey"
        assert resource["spec"]["tags"][0]["value"] == "updatedtagvalue"

        # Patch the Core Network resource, deleting the tags
        updates = {
                "spec": {"tags": []},
        }

        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        # Check resource synced successfully
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)
        
        # Check for removed user tags; system tags should persist
        core_network = networkmanager_validator.get_core_network(resource_id)
        tags.assert_ack_system_tags(
            tags=core_network["Tags"],
        )
        tags.assert_equal_without_ack_tags(
            expected=[],
            actual=core_network["Tags"],
        )
        
        # Check user tags are removed from Spec
        resource = k8s.get_resource(ref)
        assert len(resource["spec"]["tags"]) == 0

        k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)
        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref, 10, 60)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check Core Network no longer exists in AWS
        networkmanager_validator.assert_core_network(resource_id, exists=False)
