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

"""Integration tests for the GlobalNetwork API.
"""

import pytest
import time

from acktest import tags
from acktest.k8s import resource as k8s
from e2e import service_marker 
from e2e.tests.helper import NetworkManagerValidator

DESCRIPTION_DEFAULT = "Test Global Network"

CREATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10
MODIFY_WAIT_AFTER_SECONDS = 15


@service_marker
@pytest.mark.canary
class TestGlobalNetwork:
    @pytest.mark.resource_data({'tag_key': 'initialtagkey', 'tag_value': 'initialtagvalue'})
    def test_crud(self, networkmanager_client, simple_global_network):
        (ref, cr) = simple_global_network

        resource_id = cr["status"]["globalNetworkID"]

        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        # Check Global Network exists in AWS
        networkmanager_validator = NetworkManagerValidator(networkmanager_client)
        networkmanager_validator.assert_global_network(resource_id)

        # Validate description
        global_network = networkmanager_validator.get_global_network(resource_id)
        assert global_network["Description"] == DESCRIPTION_DEFAULT

        newDescription = "Updated Description"
        updates = {
            "spec": {"description": newDescription}
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)
        k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=5)
        global_network = networkmanager_validator.get_global_network(resource_id)
        assert global_network["Description"] == newDescription

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref, 2, 5)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check Global Network no longer exists in AWS
        networkmanager_validator.assert_global_network(resource_id, exists=False)

    @pytest.mark.resource_data({'tag_key': 'initialtagkey', 'tag_value': 'initialtagvalue'})
    def test_crud_tags(self, networkmanager_client, simple_global_network):
        (ref, cr) = simple_global_network
        
        resource = k8s.get_resource(ref)
        resource_id = cr["status"]["globalNetworkID"]

        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        # Check Global Network exists in AWS
        networkmanager_validator = NetworkManagerValidator(networkmanager_client)
        networkmanager_validator.assert_global_network(resource_id)
        
        # Check system and user tags exist for global network resource
        global_network = networkmanager_validator.get_global_network(resource_id)
        user_tags = {
            "initialtagkey": "initialtagvalue"
        }
        tags.assert_ack_system_tags(
            tags=global_network["Tags"],
        )
        tags.assert_equal_without_ack_tags(
            expected=user_tags,
            actual=global_network["Tags"],
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

        # Patch the GlobalNetwork, updating the tags with new pair
        updates = {
            "spec": {"tags": update_tags},
        }

        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        # Check resource synced successfully
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=5)
        
        # Check for updated user tags; system tags should persist
        global_network = networkmanager_validator.get_global_network(resource_id)
       
        updated_tags = {
            "updatedtagkey": "updatedtagvalue"
        }
        tags.assert_ack_system_tags(
            tags=global_network["Tags"],
        )
        tags.assert_equal_without_ack_tags(
            expected=updated_tags,
            actual=global_network["Tags"],
        )
               
        # Only user tags should be present in Spec
        resource = k8s.get_resource(ref)
        assert len(resource["spec"]["tags"]) == 1
        assert resource["spec"]["tags"][0]["key"] == "updatedtagkey"
        assert resource["spec"]["tags"][0]["value"] == "updatedtagvalue"

        # Patch the Global Network resource, deleting the tags
        updates = {
                "spec": {"tags": []},
        }

        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        # Check resource synced successfully
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=5)
        
        # Check for removed user tags; system tags should persist
        global_network = networkmanager_validator.get_global_network(resource_id)
        tags.assert_ack_system_tags(
            tags=global_network["Tags"],
        )
        tags.assert_equal_without_ack_tags(
            expected=[],
            actual=global_network["Tags"],
        )
        
        # Check user tags are removed from Spec
        resource = k8s.get_resource(ref)
        assert len(resource["spec"]["tags"]) == 0

        k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=5)
        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check Global Network no longer exists in AWS
        networkmanager_validator.assert_global_network(resource_id, exists=False)
