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

"""Integration tests for the ConnectPeer API.

These tests require a ConnectAttachment (which requires VpcAttachment + Cloud WAN
infrastructure). Run with --runslow.
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
from e2e.bootstrap_resources import get_bootstrap_resources


CONNECT_PEER_RESOURCE_PLURAL = "connectpeers"
CREATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 30
PEER_SYNC_WAIT_PERIODS = 30


@pytest.fixture(scope="module")
def connect_attachment_for_peer(networkmanager_client):
    """Create full Cloud WAN stack (GlobalNetwork + CoreNetwork + Policy +
    VpcAttachment + ConnectAttachment) via boto3 as prerequisites.
    Returns (connect_attachment_id, cleanup_fn)."""
    bootstrap_resources = get_bootstrap_resources()
    validator = NetworkManagerValidator(networkmanager_client)

    gn_id = validator.create_global_network(description="e2e connect peer test")

    res = networkmanager_client.create_core_network(
        GlobalNetworkId=gn_id,
        Description="e2e connect peer test core network",
    )
    cn_id = res["CoreNetwork"]["CoreNetworkId"]
    validator.wait_for_core_network_state(cn_id, "AVAILABLE", max_wait_secs=300)

    edge_location = "us-east-1"
    policy = json.dumps({
        "version": "2021.12",
        "core-network-configuration": {
            "vpn-ecmp-support": False,
            "asn-ranges": ["64512-65534"],
            "edge-locations": [{"location": edge_location}]
        },
        "segments": [{
            "name": "shared",
            "description": "Shared segment",
            "require-attachment-acceptance": False
        }]
    })
    networkmanager_client.put_core_network_policy(CoreNetworkId=cn_id, PolicyDocument=policy)
    time.sleep(10)
    try:
        policy_resp = networkmanager_client.get_core_network_policy(CoreNetworkId=cn_id)
        pv = policy_resp["CoreNetworkPolicy"]["PolicyVersionId"]
        networkmanager_client.execute_core_network_change_set(CoreNetworkId=cn_id, PolicyVersionId=pv)
    except Exception as e:
        logging.warning(f"Could not auto-execute policy: {e}")
    validator.wait_for_core_network_state(cn_id, "AVAILABLE", max_wait_secs=300)

    # Create VpcAttachment as transport
    vpc_res = networkmanager_client.create_vpc_attachment(
        CoreNetworkId=cn_id,
        VpcArn=bootstrap_resources.VpcArn,
        SubnetArns=[bootstrap_resources.SubnetArn1, bootstrap_resources.SubnetArn2],
    )
    transport_id = vpc_res["VpcAttachment"]["Attachment"]["AttachmentId"]
    validator.wait_for_attachment_state(transport_id, "AVAILABLE", max_wait_secs=300)

    # Create ConnectAttachment
    conn_res = networkmanager_client.create_connect_attachment(
        CoreNetworkId=cn_id,
        EdgeLocation=edge_location,
        TransportAttachmentId=transport_id,
        Options={"Protocol": "GRE"},
    )
    connect_id = conn_res["ConnectAttachment"]["Attachment"]["AttachmentId"]
    validator.wait_for_attachment_state(connect_id, "AVAILABLE", max_wait_secs=300)

    yield connect_id

    # Cleanup in reverse order
    for att_id in [connect_id, transport_id]:
        try:
            networkmanager_client.delete_attachment(AttachmentId=att_id)
            time.sleep(30)
        except Exception:
            pass
    try:
        networkmanager_client.delete_core_network(CoreNetworkId=cn_id)
        time.sleep(30)
    except Exception:
        pass
    validator.delete_global_network(gn_id)


@pytest.fixture
def simple_connect_peer(request, networkmanager_client, connect_attachment_for_peer):
    connect_attachment_id = connect_attachment_for_peer

    resource_name = random_suffix_name("conn-peer-ack-test", 31)
    replacements = REPLACEMENT_VALUES.copy()
    replacements["CONNECT_PEER_NAME"] = resource_name
    replacements["CONNECT_ATTACHMENT_ID"] = connect_attachment_id
    replacements["PEER_ADDRESS"] = "10.0.0.1"
    replacements["CORE_NETWORK_ADDRESS"] = "10.0.0.2"
    replacements["INSIDE_CIDR_BLOCK"] = "169.254.100.0/29"
    replacements["PEER_ASN"] = "65000"

    marker = request.node.get_closest_marker("resource_data")
    if marker is not None:
        data = marker.args[0]
        if 'tag_key' in data:
            replacements["TAG_KEY"] = data['tag_key']
        if 'tag_value' in data:
            replacements["TAG_VALUE"] = data['tag_value']

    resource_data = load_networkmanager_resource(
        "connect_peer",
        additional_replacements=replacements,
    )
    logging.debug(resource_data)

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, CONNECT_PEER_RESOURCE_PLURAL,
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
class TestConnectPeer:
    @pytest.mark.resource_data({'tag_key': 'initialtagkey', 'tag_value': 'initialtagvalue'})
    def test_crud(self, networkmanager_client, simple_connect_peer):
        (ref, cr) = simple_connect_peer

        connect_peer_id = cr["status"]["connectPeerID"]
        assert connect_peer_id is not None

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=PEER_SYNC_WAIT_PERIODS)

        validator = NetworkManagerValidator(networkmanager_client)
        validator.assert_connect_peer(connect_peer_id)

        connect_peer = validator.get_connect_peer(connect_peer_id)
        assert connect_peer is not None
        assert connect_peer.get("State") == "AVAILABLE"

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref, 2, 30)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)
        validator.assert_connect_peer(connect_peer_id, exists=False)
