#!/usr/bin/env python3
"""Build the AutoDQA sandbox microVM image (Phase 1 of the Cloudflare->MicroVM
sandbox migration).

Zips this dir's Dockerfile + server.py, uploads to S3, and calls the Lambda
MicroVMs build API to snapshot an image with pytds baked in. Writes the image ARN
to .microvm_config.json for the broker (Phase 2) to run-microvm from. Idempotent:
reuses the S3 bucket + build role, and updates the image if it already exists.

    AWS_PROFILE=bigarc-autodqa AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python build_image.py

API shapes confirmed against boto3 1.43.42's lambda-microvms model (name,
codeArtifact={"uri":...}, baseImageArn, buildRoleArn, egressNetworkConnectors,
imageIdentifier; Get returns imageArn/state). REQUIRES boto3 >= ~1.43.42 — older
boto3 doesn't know the service at all. The egress connector is on the *build* so the
Dockerfile can fetch deps; the running VM gets its own egress at run-microvm (Phase 2).
"""
import io
import json
import os
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
HERE = Path(__file__).resolve().parent
CONFIG_OUT = HERE / ".microvm_config.json"

IMAGE_NAME = "autodqa-sandbox"
ROLE_NAME = "autodqa-microvm-build-role"
BASE_IMAGE_ARN = f"arn:aws:lambda:{REGION}:aws:microvm-image:al2023-1"
# Lambda-managed egress connector — gives the BUILD internet access so the
# Dockerfile's `dnf install` / `pip install python-tds` can reach the repos.
INTERNET_EGRESS = f"arn:aws:lambda:{REGION}:aws:network-connector:aws-network-connector:INTERNET_EGRESS"


def ensure_bucket(s3, account):
    bucket = f"autodqa-microvm-build-{account}"
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"reusing bucket {bucket}")
    except Exception:
        kw = {} if REGION == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": REGION}}
        s3.create_bucket(Bucket=bucket, **kw)
        print(f"created bucket {bucket}")
    return bucket


def ensure_build_role(iam, bucket):
    """The role Lambda assumes to pull the artifact from S3 + write build logs."""
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": ["sts:AssumeRole", "sts:TagSession"]}]}
    try:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"reusing build role {ROLE_NAME}")
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=ROLE_NAME,
                              AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        print(f"created build role {ROLE_NAME}; waiting for IAM propagation...")
        time.sleep(12)
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="autodqa-microvm-build",
        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": f"arn:aws:s3:::{bucket}/*"},
            {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                                           "logs:PutLogEvents"], "Resource": "arn:aws:logs:*:*:*"}]}))
    return arn


def upload_artifact(s3, bucket):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(HERE / "Dockerfile", "Dockerfile")
        z.write(HERE / "server.py", "server.py")
    key = "autodqa-sandbox/app.zip"
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
    print(f"uploaded artifact s3://{bucket}/{key} ({len(buf.getvalue())/1024:.0f} KiB)")
    return f"s3://{bucket}/{key}"


def build_image(mv, uri, role_arn, image_arn):
    # create takes a `name`; get/update take the full `imageIdentifier` ARN.
    try:
        state = mv.get_microvm_image(imageIdentifier=image_arn).get("state")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("ResourceNotFoundException", "NotFoundException"):
            state = None
        else:
            raise

    in_progress = ("CREATING", "UPDATING", "PENDING", "IN_PROGRESS")
    if state is None:
        print(f"creating image {IMAGE_NAME}")
        mv.create_microvm_image(name=IMAGE_NAME, codeArtifact={"uri": uri},
                                baseImageArn=BASE_IMAGE_ARN, buildRoleArn=role_arn,
                                egressNetworkConnectors=[INTERNET_EGRESS])
    elif state in in_progress:
        print(f"image {IMAGE_NAME} already building ({state}); polling...")
    else:
        print(f"updating existing image {IMAGE_NAME} (was {state})")
        mv.update_microvm_image(imageIdentifier=image_arn, codeArtifact={"uri": uri},
                                baseImageArn=BASE_IMAGE_ARN, buildRoleArn=role_arn,
                                egressNetworkConnectors=[INTERNET_EGRESS],
                                description="autodqa sandbox (pytds baked in)")

    for _ in range(120):  # poll up to ~10 min
        img = mv.get_microvm_image(imageIdentifier=image_arn)
        st = img.get("state")
        if st in ("CREATED", "UPDATED"):
            return img
        if st in ("CREATION_FAILED", "UPDATE_FAILED"):
            raise SystemExit(f"build failed: {img.get('stateReason')} "
                             f"(logs: /aws/lambda/microvms/{IMAGE_NAME})")
        time.sleep(5)
    raise SystemExit("timed out waiting for image build")


def main():
    account = boto3.client("sts").get_caller_identity()["Account"]
    s3 = boto3.client("s3", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    mv = boto3.client("lambda-microvms", region_name=REGION)

    image_arn = f"arn:aws:lambda:{REGION}:{account}:microvm-image:{IMAGE_NAME}"
    bucket = ensure_bucket(s3, account)
    role_arn = ensure_build_role(iam, bucket)
    uri = upload_artifact(s3, bucket)
    img = build_image(mv, uri, role_arn, image_arn)

    CONFIG_OUT.write_text(json.dumps({
        "region": REGION, "image_name": IMAGE_NAME, "image_arn": img.get("imageArn"),
        "base_image_arn": BASE_IMAGE_ARN, "build_role_arn": role_arn, "s3_bucket": bucket,
    }, indent=2))
    print(f"\nimage ready: {img.get('imageArn')}")
    print(f"wrote {CONFIG_OUT}")
    print("next (Phase 2): point sandbox_client.py at run-microvm from this image ARN")


if __name__ == "__main__":
    main()
