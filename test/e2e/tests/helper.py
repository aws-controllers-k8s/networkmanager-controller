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
