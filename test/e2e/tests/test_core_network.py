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
import logging

from acktest import tags
from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from e2e import service_marker
from e2e.tests.helper import NetworkManagerValidator
from e2e import CRD_GROUP, CRD_VERSION, load_networkmanager_resource
from e2e.replacement_values import REPLACEMENT_VALUES


DESCRIPTION_DEFAULT = "Test Core Network"
CORE_NETWORK_RESOURCE_PLURAL = "corenetworks"
CREATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10
MODIFY_WAIT_AFTER_SECONDS = 15
# CoreNetwork creation can take a while to become AVAILABLE
CORE_NETWORK_AVAILABLE_WAIT_PERIODS = 20


@pytest.fixture
def global_network_for_core_network(networkmanager_client):
    """Create a GlobalNetwork via boto3 as a prerequisite for CoreNetwork tests."""
    validator = NetworkManagerValidator(networkmanager_client)
    gn_id = validator.create_global_network(description="e2e core network test prerequisite")
    yield gn_id
    validator.delete_global_network(gn_id)


@pytest.fixture
def simple_core_network(request, networkmanager_client, global_network_for_core_network):
    global_network_id = global_network_for_core_network
    resource_name = random_suffix_name("core-network-ack-test", 31)
    replacements = REPLACEMENT_VALUES.copy()
    replacements["CORE_NETWORK_NAME"] = resource_name
    replacements["GLOBAL_NETWORK_ID"] = global_network_id
    replacements["DESCRIPTION"] = DESCRIPTION_DEFAULT

    marker = request.node.get_closest_marker("resource_data")
    if marker is not None:
        data = marker.args[0]
        if 'description' in data:
            replacements["DESCRIPTION"] = data['description']
        if 'tag_key' in data:
            replacements["TAG_KEY"] = data['tag_key']
        if 'tag_value' in data:
            replacements["TAG_VALUE"] = data['tag_value']

    resource_data = load_networkmanager_resource(
        "core_network",
        additional_replacements=replacements,
    )
    logging.debug(resource_data)

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
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted
    except:
        pass


@service_marker
@pytest.mark.canary
class TestCoreNetwork:
    @pytest.mark.resource_data({'tag_key': 'initialtagkey', 'tag_value': 'initialtagvalue'})
    def test_crud(self, networkmanager_client, simple_core_network):
        (ref, cr) = simple_core_network

        core_network_id = cr["status"]["coreNetworkID"]

        # Wait for CoreNetwork to become AVAILABLE
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=CORE_NETWORK_AVAILABLE_WAIT_PERIODS)

        # Check CoreNetwork exists in AWS
        validator = NetworkManagerValidator(networkmanager_client)
        validator.assert_core_network(core_network_id)

        # Validate description
        core_network = validator.get_core_network(core_network_id)
        assert core_network["Description"] == DESCRIPTION_DEFAULT

        # Update description
        new_description = "Updated Core Network Description"
        updates = {
            "spec": {"description": new_description}
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=CORE_NETWORK_AVAILABLE_WAIT_PERIODS)
        core_network = validator.get_core_network(core_network_id)
        assert core_network["Description"] == new_description

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref, 2, 5)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check CoreNetwork no longer exists in AWS
        validator.assert_core_network(core_network_id, exists=False)

    @pytest.mark.resource_data({'tag_key': 'initialtagkey', 'tag_value': 'initialtagvalue'})
    def test_crud_tags(self, networkmanager_client, simple_core_network):
        (ref, cr) = simple_core_network

        resource = k8s.get_resource(ref)
        core_network_id = cr["status"]["coreNetworkID"]

        # Wait for CoreNetwork to become AVAILABLE
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=CORE_NETWORK_AVAILABLE_WAIT_PERIODS)

        # Check CoreNetwork exists in AWS
        validator = NetworkManagerValidator(networkmanager_client)
        validator.assert_core_network(core_network_id)

        # Check system and user tags
        core_network = validator.get_core_network(core_network_id)
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
        updates = {
            "spec": {"tags": update_tags},
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=CORE_NETWORK_AVAILABLE_WAIT_PERIODS)

        core_network = validator.get_core_network(core_network_id)
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

        resource = k8s.get_resource(ref)
        assert len(resource["spec"]["tags"]) == 1
        assert resource["spec"]["tags"][0]["key"] == "updatedtagkey"
        assert resource["spec"]["tags"][0]["value"] == "updatedtagvalue"

        # Remove tags
        updates = {
            "spec": {"tags": []},
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=CORE_NETWORK_AVAILABLE_WAIT_PERIODS)

        core_network = validator.get_core_network(core_network_id)
        tags.assert_ack_system_tags(
            tags=core_network["Tags"],
        )
        tags.assert_equal_without_ack_tags(
            expected=[],
            actual=core_network["Tags"],
        )

        resource = k8s.get_resource(ref)
        assert len(resource["spec"]["tags"]) == 0

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)
        validator.assert_core_network(core_network_id, exists=False)
