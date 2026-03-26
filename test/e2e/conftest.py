# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
#	 http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

import boto3
import pytest
import logging
import time

from acktest.resources import random_suffix_name
from acktest.k8s import resource as k8s
from e2e import CRD_GROUP, CRD_VERSION, load_networkmanager_resource
from e2e.replacement_values import REPLACEMENT_VALUES

GLOBAL_NETWORK_RESOURCE_PLURAL = "globalnetworks"
GLOBAL_NETWORK_CREATE_WAIT_AFTER_SECONDS = 10

def pytest_addoption(parser):
    parser.addoption("--runslow", action="store_true", default=False, help="run slow tests")

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "canary: mark test to also run in canary tests"
    )
    config.addinivalue_line(
        "markers", "service(arg): mark test associated with a given service"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow to run"
    )
    config.addinivalue_line(
        "markers", "resource_data: mark test with data to use when creating fixture"
    )

def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        return
    skip_slow = pytest.mark.skip(reason="need --runslow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)

# Provide a k8s client to interact with the integration test cluster
@pytest.fixture(scope='class')
def k8s_client():
    return k8s._get_k8s_api_client()

@pytest.fixture(scope='module')
def networkmanager_client():
    return boto3.client('networkmanager')


@pytest.fixture
def simple_global_network(request):
    resource_name = random_suffix_name("global-network-ack-test", 31)
    replacements = REPLACEMENT_VALUES.copy()
    replacements["GLOBAL_NETWORK_NAME"] = resource_name
    replacements["DESCRIPTION"] = "Test Global Network"

    marker = request.node.get_closest_marker("resource_data")
    if marker is not None:
        data = marker.args[0]
        if 'description' in data:
            replacements["DESCRIPTION"] = data['description']
        if 'tag_key' in data:
            replacements["TAG_KEY"] = data['tag_key']
        if 'tag_value' in data:
            replacements["TAG_VALUE"] = data['tag_value']

    # Load Global Network CR
    resource_data = load_networkmanager_resource(
        "global_network",
        additional_replacements=replacements,
    )
    logging.debug(resource_data)

    # Create k8s resource
    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, GLOBAL_NETWORK_RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    time.sleep(GLOBAL_NETWORK_CREATE_WAIT_AFTER_SECONDS)

    cr = k8s.wait_resource_consumed_by_controller(ref)
    assert cr is not None
    assert k8s.get_resource_exists(ref)

    yield (ref, cr)

    # Try to delete, if doesn't already exist
    try:
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted
    except:
        pass