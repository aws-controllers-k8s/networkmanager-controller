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
"""Bootstraps the resources required to run the NetworkManager integration tests.

Creates:
  - A VPC with two subnets (for VpcAttachment and ConnectAttachment tests)
  - A Virtual Private Gateway + VPN Connection (for SiteToSiteVpnAttachment tests)
"""
import logging
import time
import boto3

from acktest.bootstrapping import Resources, BootstrapFailureException

from e2e import bootstrap_directory
from e2e.bootstrap_resources import BootstrapResources


def service_bootstrap() -> Resources:
    logging.getLogger().setLevel(logging.INFO)

    ec2 = boto3.client("ec2")
    sts = boto3.client("sts")

    account_id = sts.get_caller_identity()["Account"]
    region = ec2.meta.region_name

    # Create VPC
    logging.info("Creating VPC...")
    vpc_resp = ec2.create_vpc(CidrBlock="10.99.0.0/16")
    vpc_id = vpc_resp["Vpc"]["VpcId"]
    vpc_arn = f"arn:aws:ec2:{region}:{account_id}:vpc/{vpc_id}"
    logging.info(f"Created VPC: {vpc_id}")

    # Wait for VPC to be available
    ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id])

    # Get availability zones
    azs = ec2.describe_availability_zones(
        Filters=[{"Name": "state", "Values": ["available"]}]
    )["AvailabilityZones"]

    # Create two subnets in different AZs
    logging.info("Creating subnets...")
    subnet1_resp = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.99.1.0/24",
        AvailabilityZone=azs[0]["ZoneName"],
    )
    subnet1_id = subnet1_resp["Subnet"]["SubnetId"]
    subnet1_arn = f"arn:aws:ec2:{region}:{account_id}:subnet/{subnet1_id}"

    subnet2_resp = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.99.2.0/24",
        AvailabilityZone=azs[1]["ZoneName"] if len(azs) > 1 else azs[0]["ZoneName"],
    )
    subnet2_id = subnet2_resp["Subnet"]["SubnetId"]
    subnet2_arn = f"arn:aws:ec2:{region}:{account_id}:subnet/{subnet2_id}"
    logging.info(f"Created subnets: {subnet1_id}, {subnet2_id}")

    # Create Virtual Private Gateway for VPN Connection
    logging.info("Creating Virtual Private Gateway...")
    vgw_resp = ec2.create_vpn_gateway(Type="ipsec.1")
    vgw_id = vgw_resp["VpnGateway"]["VpnGatewayId"]
    logging.info(f"Created VPN Gateway: {vgw_id}")

    # Create Customer Gateway (required for VPN Connection)
    logging.info("Creating Customer Gateway...")
    cgw_resp = ec2.create_customer_gateway(
        Type="ipsec.1", BgpAsn=65000, IpAddress="198.51.100.1",
    )
    cgw_id = cgw_resp["CustomerGateway"]["CustomerGatewayId"]
    logging.info(f"Created Customer Gateway: {cgw_id}")

    # Create VPN Connection
    logging.info("Creating VPN Connection...")
    vpn_resp = ec2.create_vpn_connection(
        Type="ipsec.1",
        CustomerGatewayId=cgw_id,
        VpnGatewayId=vgw_id,
    )
    vpn_id = vpn_resp["VpnConnection"]["VpnConnectionId"]
    vpn_arn = f"arn:aws:ec2:{region}:{account_id}:vpn-connection/{vpn_id}"
    logging.info(f"Created VPN Connection: {vpn_id} (ARN: {vpn_arn})")

    # Wait for VPN to be available
    logging.info("Waiting for VPN Connection to become available...")
    ec2.get_waiter("vpn_connection_available").wait(VpnConnectionIds=[vpn_id])
    logging.info("VPN Connection is available.")

    resources = BootstrapResources(
        VpcArn=vpc_arn,
        SubnetArn1=subnet1_arn,
        SubnetArn2=subnet2_arn,
        VpnConnectionArn=vpn_arn,
    )

    # Store IDs for cleanup (not part of dataclass but stored in pickle metadata)
    resources._vpc_id = vpc_id
    resources._subnet1_id = subnet1_id
    resources._subnet2_id = subnet2_id
    resources._vgw_id = vgw_id
    resources._cgw_id = cgw_id
    resources._vpn_id = vpn_id

    return resources


if __name__ == "__main__":
    config = service_bootstrap()
    # Write config to current directory by default
    config.serialize(bootstrap_directory)
