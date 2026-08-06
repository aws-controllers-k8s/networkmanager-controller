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

"""Cleans up the resources created by the bootstrapping process.
"""

import logging
import time
import boto3

from e2e import bootstrap_directory
from e2e.bootstrap_resources import BootstrapResources


def service_cleanup():
    logging.getLogger().setLevel(logging.INFO)
    ec2 = boto3.client("ec2")

    resources = BootstrapResources.deserialize(bootstrap_directory)

    # Delete VPN Connection
    vpn_id = getattr(resources, "_vpn_id", None)
    if vpn_id:
        logging.info(f"Deleting VPN Connection: {vpn_id}")
        try:
            ec2.delete_vpn_connection(VpnConnectionId=vpn_id)
            ec2.get_waiter("vpn_connection_deleted").wait(VpnConnectionIds=[vpn_id])
        except Exception as e:
            logging.warning(f"Failed to delete VPN Connection: {e}")

    # Delete Customer Gateway
    cgw_id = getattr(resources, "_cgw_id", None)
    if cgw_id:
        logging.info(f"Deleting Customer Gateway: {cgw_id}")
        try:
            ec2.delete_customer_gateway(CustomerGatewayId=cgw_id)
        except Exception as e:
            logging.warning(f"Failed to delete Customer Gateway: {e}")

    # Delete VPN Gateway
    vgw_id = getattr(resources, "_vgw_id", None)
    if vgw_id:
        logging.info(f"Deleting VPN Gateway: {vgw_id}")
        try:
            ec2.delete_vpn_gateway(VpnGatewayId=vgw_id)
        except Exception as e:
            logging.warning(f"Failed to delete VPN Gateway: {e}")

    # Delete Subnets
    for attr in ["_subnet1_id", "_subnet2_id"]:
        subnet_id = getattr(resources, attr, None)
        if subnet_id:
            logging.info(f"Deleting Subnet: {subnet_id}")
            try:
                ec2.delete_subnet(SubnetId=subnet_id)
            except Exception as e:
                logging.warning(f"Failed to delete Subnet: {e}")

    # Delete VPC
    vpc_id = getattr(resources, "_vpc_id", None)
    if vpc_id:
        logging.info(f"Deleting VPC: {vpc_id}")
        try:
            ec2.delete_vpc(VpcId=vpc_id)
        except Exception as e:
            logging.warning(f"Failed to delete VPC: {e}")

    logging.info("Cleanup complete.")


if __name__ == "__main__":
    service_cleanup()
