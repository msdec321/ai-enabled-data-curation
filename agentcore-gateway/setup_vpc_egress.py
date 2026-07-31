#!/usr/bin/env python3
"""Provision the VPC egress path for the sandbox MicroVMs: an operator IAM role
(lets the Lambda networking service manage ENIs in our VPC) plus a customer-
managed VPC egress network connector. The connector's ENIs live in the same
subnets IT attached to the broker Lambda — the ones the institutional firewall
was opened for — so MicroVM traffic to the institutional network sources from
an approved address.

Idempotent: reuses the role/connector if they exist. Prints the connector ARN
and sets MICROVM_EGRESS_CONNECTORS on the broker Lambda (sandbox_client.py
reads it at launch time; INTERNET_EGRESS remains the default when unset).

    AWS_PROFILE=bigarc-autodqa AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python setup_vpc_egress.py
"""
import json
import os
import time

import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
LAMBDA_NAME = "autodqa-run-python"          # broker Lambda; we mirror its VPC config
ROLE_NAME = "autodqa-nc-operator-role"
CONNECTOR_NAME = "autodqa-microvm-vpc-egress"

OPERATOR_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "CreateENI",
            "Effect": "Allow",
            "Action": "ec2:CreateNetworkInterface",
            "Resource": [
                "arn:aws:ec2:*:*:network-interface/*",
                "arn:aws:ec2:*:*:subnet/*",
                "arn:aws:ec2:*:*:security-group/*",
            ],
        },
        {
            "Sid": "TagENI",
            "Effect": "Allow",
            "Action": "ec2:CreateTags",
            "Resource": "arn:aws:ec2:*:*:network-interface/*",
            "Condition": {"StringEquals": {
                "ec2:ManagedResourceOperator": "network-connectors.lambda.amazonaws.com"}},
        },
        {
            # ENI managers also need to look up the VPC pieces and clean up
            # their ENIs (Describe* has no resource-level support).
            "Sid": "DescribeVpcPieces",
            "Effect": "Allow",
            "Action": ["ec2:DescribeNetworkInterfaces", "ec2:DescribeSubnets",
                       "ec2:DescribeSecurityGroups", "ec2:DescribeVpcs"],
            "Resource": "*",
        },
        {
            "Sid": "DeleteENI",
            "Effect": "Allow",
            "Action": "ec2:DeleteNetworkInterface",
            "Resource": "arn:aws:ec2:*:*:network-interface/*",
        },
    ],
}


def ensure_operator_role(iam) -> str:
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "network-connectors.lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"}]}
    try:
        arn = iam.create_role(RoleName=ROLE_NAME,
                              AssumeRolePolicyDocument=json.dumps(trust),
                              Description="Lets Lambda network connectors manage ENIs "
                                          "for the AutoDQA sandbox VPC egress")["Role"]["Arn"]
        print(f"created role {ROLE_NAME}")
        time.sleep(8)  # IAM propagation before the connector service assumes it
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"reusing role {ROLE_NAME}")
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="autodqa-nc-eni",
                        PolicyDocument=json.dumps(OPERATOR_POLICY))
    return arn


def ensure_connector(core, operator_role_arn: str, vpc_cfg: dict) -> str:
    existing = next((nc for nc in core.list_network_connectors()
                     .get("NetworkConnectors", [])
                     if nc.get("Name") == CONNECTOR_NAME), None)
    if existing:
        print(f"reusing connector {CONNECTOR_NAME} ({existing['State']})")
        return existing["Arn"]
    # Retry: fresh IAM roles/policies can take ~10-60s to propagate to EC2 auth.
    for attempt in range(6):
        try:
            r = core.create_network_connector(
                Name=CONNECTOR_NAME,
                Configuration={"VpcEgressConfiguration": {
                    "SubnetIds": vpc_cfg["SubnetIds"],
                    "SecurityGroupIds": vpc_cfg["SecurityGroupIds"],
                    "NetworkProtocol": "IPv4",
                    "AssociatedComputeResourceTypes": ["MicroVm"],
                }},
                OperatorRole=operator_role_arn,
            )
            break
        except core.exceptions.InvalidParameterValueException:
            if attempt == 5:
                raise
            print("  role permissions not visible to EC2 yet, retrying in 15s...")
            time.sleep(15)
    arn = r["Arn"]
    print(f"created connector {CONNECTOR_NAME}")
    return arn


def wait_active(core, arn: str, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while True:
        nc = core.get_network_connector(Identifier=arn)
        state = nc["State"]
        if state == "ACTIVE":
            print("connector ACTIVE")
            return
        if state in ("FAILED", "DELETE_FAILED"):
            raise RuntimeError(f"connector {state}: {nc.get('StateReason')}")
        if time.time() > deadline:
            raise TimeoutError(f"connector still {state} after {timeout}s")
        time.sleep(10)


def main():
    iam = boto3.client("iam")
    core = boto3.client("lambda-core", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)

    vpc_cfg = lam.get_function_configuration(FunctionName=LAMBDA_NAME).get("VpcConfig")
    if not vpc_cfg or not vpc_cfg.get("SubnetIds"):
        raise SystemExit(f"{LAMBDA_NAME} has no VPC config — attach it first "
                         "(IT did this via the console) so we can mirror it")
    print(f"mirroring VPC config: {vpc_cfg['VpcId']} subnets={vpc_cfg['SubnetIds']}")

    role_arn = ensure_operator_role(iam)
    arn = ensure_connector(core, role_arn, vpc_cfg)
    wait_active(core, arn)

    env = lam.get_function_configuration(FunctionName=LAMBDA_NAME)["Environment"]["Variables"]
    env["MICROVM_EGRESS_CONNECTORS"] = arn
    lam.update_function_configuration(FunctionName=LAMBDA_NAME, Environment={"Variables": env})
    lam.get_waiter("function_updated_v2").wait(FunctionName=LAMBDA_NAME)
    print(f"MICROVM_EGRESS_CONNECTORS -> {arn}")
    print("\ndone — new sandboxes will egress through the VPC")


if __name__ == "__main__":
    main()
