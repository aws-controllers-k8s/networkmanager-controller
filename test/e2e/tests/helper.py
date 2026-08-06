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

"""Helper functions for network manager tests
"""

from typing import Union, Dict


class NetworkManagerValidator:
    def __init__(self, networkmanager_client):
        self.networkmanager_client = networkmanager_client

    def get_global_network(self, global_network_id: str) -> Union[None, Dict]:
        try:
            aws_res = self.networkmanager_client.describe_global_networks(GlobalNetworkIds=[global_network_id])
            if "GlobalNetworks" in aws_res and len(aws_res["GlobalNetworks"]) > 0:
                return aws_res["GlobalNetworks"][0]
            return None
        except self.networkmanager_client.exceptions.ClientError:
            return None

    def assert_global_network(self, global_network_id: str, exists=True):
        res_found = False
        try:
            aws_res = self.networkmanager_client.describe_global_networks(GlobalNetworkIds=[global_network_id])
            res_found = "GlobalNetworks" in aws_res and len(aws_res["GlobalNetworks"]) > 0
        except self.networkmanager_client.exceptions.ClientError:
            pass
        assert res_found is exists

    def get_core_network(self, core_network_id: str) -> Union[None, Dict]:
        try:
            aws_res = self.networkmanager_client.get_core_network(CoreNetworkId=core_network_id)
            return aws_res.get("CoreNetwork")
        except self.networkmanager_client.exceptions.ResourceNotFoundException:
            return None
        except self.networkmanager_client.exceptions.ClientError:
            return None

    def assert_core_network(self, core_network_id: str, exists=True):
        res = self.get_core_network(core_network_id)
        assert (res is not None) is exists

    def get_core_network_policy(self, core_network_id: str) -> Union[None, Dict]:
        try:
            aws_res = self.networkmanager_client.get_core_network_policy(CoreNetworkId=core_network_id)
            return aws_res.get("CoreNetworkPolicy")
        except self.networkmanager_client.exceptions.ResourceNotFoundException:
            return None
        except self.networkmanager_client.exceptions.ClientError:
            return None

    def assert_core_network_policy(self, core_network_id: str, exists=True):
        res = self.get_core_network_policy(core_network_id)
        assert (res is not None) is exists

    def get_attachment(self, attachment_id: str) -> Union[None, Dict]:
        try:
            aws_res = self.networkmanager_client.get_vpc_attachment(AttachmentId=attachment_id)
            return aws_res.get("VpcAttachment")
        except Exception:
            pass
        try:
            aws_res = self.networkmanager_client.get_connect_attachment(AttachmentId=attachment_id)
            return aws_res.get("ConnectAttachment")
        except Exception:
            pass
        try:
            aws_res = self.networkmanager_client.get_site_to_site_vpn_attachment(AttachmentId=attachment_id)
            return aws_res.get("SiteToSiteVpnAttachment")
        except Exception:
            pass
        return None

    def get_vpc_attachment(self, attachment_id: str) -> Union[None, Dict]:
        try:
            aws_res = self.networkmanager_client.get_vpc_attachment(AttachmentId=attachment_id)
            return aws_res.get("VpcAttachment")
        except self.networkmanager_client.exceptions.ResourceNotFoundException:
            return None
        except self.networkmanager_client.exceptions.ClientError:
            return None

    def assert_vpc_attachment(self, attachment_id: str, exists=True):
        res = self.get_vpc_attachment(attachment_id)
        assert (res is not None) is exists

    def get_connect_attachment(self, attachment_id: str) -> Union[None, Dict]:
        try:
            aws_res = self.networkmanager_client.get_connect_attachment(AttachmentId=attachment_id)
            return aws_res.get("ConnectAttachment")
        except self.networkmanager_client.exceptions.ResourceNotFoundException:
            return None
        except self.networkmanager_client.exceptions.ClientError:
            return None

    def assert_connect_attachment(self, attachment_id: str, exists=True):
        res = self.get_connect_attachment(attachment_id)
        assert (res is not None) is exists

    def get_connect_peer(self, connect_peer_id: str) -> Union[None, Dict]:
        try:
            aws_res = self.networkmanager_client.get_connect_peer(ConnectPeerId=connect_peer_id)
            return aws_res.get("ConnectPeer")
        except self.networkmanager_client.exceptions.ResourceNotFoundException:
            return None
        except self.networkmanager_client.exceptions.ClientError:
            return None

    def assert_connect_peer(self, connect_peer_id: str, exists=True):
        res = self.get_connect_peer(connect_peer_id)
        assert (res is not None) is exists

    def get_site_to_site_vpn_attachment(self, attachment_id: str) -> Union[None, Dict]:
        try:
            aws_res = self.networkmanager_client.get_site_to_site_vpn_attachment(AttachmentId=attachment_id)
            return aws_res.get("SiteToSiteVpnAttachment")
        except self.networkmanager_client.exceptions.ResourceNotFoundException:
            return None
        except self.networkmanager_client.exceptions.ClientError:
            return None

    def assert_site_to_site_vpn_attachment(self, attachment_id: str, exists=True):
        res = self.get_site_to_site_vpn_attachment(attachment_id)
        assert (res is not None) is exists

    def wait_for_core_network_state(self, core_network_id: str, target_state: str, max_wait_secs: int = 300, interval_secs: int = 15):
        """Poll until core network reaches target state or timeout."""
        import time
        elapsed = 0
        while elapsed < max_wait_secs:
            cn = self.get_core_network(core_network_id)
            if cn and cn.get("State") == target_state:
                return cn
            time.sleep(interval_secs)
            elapsed += interval_secs
        raise TimeoutError(f"CoreNetwork {core_network_id} did not reach state {target_state} within {max_wait_secs}s")

    def wait_for_attachment_state(self, attachment_id: str, target_state: str, max_wait_secs: int = 300, interval_secs: int = 15):
        """Poll until attachment reaches target state or timeout."""
        import time
        elapsed = 0
        while elapsed < max_wait_secs:
            att = self.get_attachment(attachment_id)
            if att:
                # Attachment state may be nested under .Attachment.State or at top level
                state = None
                if "Attachment" in att:
                    state = att["Attachment"].get("State")
                else:
                    state = att.get("State")
                if state == target_state:
                    return att
            time.sleep(interval_secs)
            elapsed += interval_secs
        raise TimeoutError(f"Attachment {attachment_id} did not reach state {target_state} within {max_wait_secs}s")

    def create_global_network(self, description: str = "e2e test global network") -> str:
        """Create a GlobalNetwork directly via boto3. Returns the GlobalNetworkId."""
        res = self.networkmanager_client.create_global_network(Description=description)
        return res["GlobalNetwork"]["GlobalNetworkId"]

    def delete_global_network(self, global_network_id: str):
        """Delete a GlobalNetwork directly via boto3."""
        try:
            self.networkmanager_client.delete_global_network(GlobalNetworkId=global_network_id)
        except Exception:
            pass
