#!/usr/bin/env python3
"""Provision the AutoDQA front-end: a Cognito USER pool + public web app client
(login), a private S3 bucket (static assets), and a CloudFront distribution
(HTTPS via Origin Access Control). Idempotent — reuses anything already created.
Writes frontend/.frontend_config.json.

    AWS_PROFILE=autodqa-admin AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python setup_frontend.py

Optional seed user so login works right away (password: >=8 chars, upper/lower/number):
    AUTODQA_UI_USER=you@example.com AUTODQA_UI_PASSWORD='Str0ngPass1' ... setup_frontend.py
"""
import json
import os
from pathlib import Path

import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
HERE = Path(__file__).resolve().parent
CONFIG_OUT = HERE / ".frontend_config.json"

POOL_NAME = "autodqa-users"
CLIENT_NAME = "autodqa-web"
OAC_NAME = "autodqa-frontend-oac"
CF_COMMENT = "autodqa-frontend"
ORIGIN_ID = "s3-autodqa-frontend"
# AWS managed cache policy "CachingOptimized"
CACHE_POLICY_CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"


# ----------------------------- Cognito -----------------------------
def _find_user_pool(idp):
    token = None
    while True:
        page = idp.list_user_pools(MaxResults=60, **({"NextToken": token} if token else {}))
        for p in page.get("UserPools", []):
            if p["Name"] == POOL_NAME:
                return p["Id"]
        token = page.get("NextToken")
        if not token:
            return None


def _find_client(idp, pool_id):
    token = None
    while True:
        page = idp.list_user_pool_clients(
            UserPoolId=pool_id, MaxResults=60, **({"NextToken": token} if token else {}))
        for c in page.get("UserPoolClients", []):
            if c["ClientName"] == CLIENT_NAME:
                return c["ClientId"]
        token = page.get("NextToken")
        if not token:
            return None


def setup_cognito(idp):
    pool_id = _find_user_pool(idp)
    if pool_id:
        print(f"reusing user pool {pool_id}")
    else:
        pool_id = idp.create_user_pool(
            PoolName=POOL_NAME,
            Policies={"PasswordPolicy": {"MinimumLength": 8, "RequireUppercase": True,
                                         "RequireLowercase": True, "RequireNumbers": True,
                                         "RequireSymbols": False}},
            AutoVerifiedAttributes=["email"], UsernameAttributes=["email"],
        )["UserPool"]["Id"]
        print(f"created user pool {pool_id}")

    client_id = _find_client(idp, pool_id)
    if client_id:
        print(f"reusing app client {client_id}")
    else:
        client_id = idp.create_user_pool_client(
            UserPoolId=pool_id, ClientName=CLIENT_NAME, GenerateSecret=False,
            ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        )["UserPoolClient"]["ClientId"]
        print(f"created app client {client_id}")

    user, pw = os.environ.get("AUTODQA_UI_USER"), os.environ.get("AUTODQA_UI_PASSWORD")
    if user and pw:
        try:
            idp.admin_create_user(
                UserPoolId=pool_id, Username=user, MessageAction="SUPPRESS",
                UserAttributes=[{"Name": "email", "Value": user},
                                {"Name": "email_verified", "Value": "true"}])
        except idp.exceptions.UsernameExistsException:
            pass
        idp.admin_set_user_password(UserPoolId=pool_id, Username=user, Password=pw, Permanent=True)
        print(f"seed user ready: {user}")

    discovery_url = f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    return {
        "region": REGION, "user_pool_id": pool_id, "client_id": client_id,
        "discovery_url": discovery_url,
        "authorizer_config": {"customJWTAuthorizer": {
            "discoveryUrl": discovery_url, "allowedClients": [client_id]}},
    }


# ----------------------------- S3 + CloudFront -----------------------------
def setup_hosting(s3, cf, account_id):
    bucket = f"autodqa-frontend-{account_id}"
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"reusing bucket {bucket}")
    except Exception:
        kw = {} if REGION == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": REGION}}
        s3.create_bucket(Bucket=bucket, **kw)
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True,
            "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        print(f"created private bucket {bucket}")

    # Origin Access Control (lets only CloudFront read the private bucket).
    oac_id = next((o["Id"] for o in cf.list_origin_access_controls()
                   .get("OriginAccessControlList", {}).get("Items", [])
                   if o["Name"] == OAC_NAME), None)
    if oac_id:
        print(f"reusing OAC {oac_id}")
    else:
        oac_id = cf.create_origin_access_control(OriginAccessControlConfig={
            "Name": OAC_NAME, "OriginAccessControlOriginType": "s3",
            "SigningBehavior": "always", "SigningProtocol": "sigv4",
        })["OriginAccessControl"]["Id"]
        print(f"created OAC {oac_id}")

    # Distribution (find by comment).
    existing = next((d for d in cf.list_distributions()
                     .get("DistributionList", {}).get("Items", [])
                     if d.get("Comment") == CF_COMMENT), None)
    if existing:
        dist_id, domain = existing["Id"], existing["DomainName"]
        print(f"reusing distribution {dist_id} ({domain})")
    else:
        res = cf.create_distribution(DistributionConfig={
            "CallerReference": f"autodqa-frontend-{account_id}",
            "Comment": CF_COMMENT, "Enabled": True, "DefaultRootObject": "index.html",
            "Origins": {"Quantity": 1, "Items": [{
                "Id": ORIGIN_ID,
                "DomainName": f"{bucket}.s3.{REGION}.amazonaws.com",
                "OriginAccessControlId": oac_id,
                "S3OriginConfig": {"OriginAccessIdentity": ""},
            }]},
            "DefaultCacheBehavior": {
                "TargetOriginId": ORIGIN_ID,
                "ViewerProtocolPolicy": "redirect-to-https",
                "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"],
                                   "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
                "Compress": True,
                "CachePolicyId": CACHE_POLICY_CACHING_OPTIMIZED,
            },
            "PriceClass": "PriceClass_100",
            "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
        })["Distribution"]
        dist_id, domain = res["Id"], res["DomainName"]
        print(f"created distribution {dist_id} ({domain})")

    # Bucket policy: allow ONLY this CloudFront distribution (via OAC) to read.
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AllowCloudFrontOAC", "Effect": "Allow",
            "Principal": {"Service": "cloudfront.amazonaws.com"},
            "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/*",
            "Condition": {"StringEquals": {
                "AWS:SourceArn": f"arn:aws:cloudfront::{account_id}:distribution/{dist_id}"}},
        }],
    }))
    print("bucket policy set (CloudFront OAC read-only)")

    return {"s3_bucket": bucket, "cloudfront_distribution_id": dist_id,
            "cloudfront_domain": domain, "cloudfront_url": f"https://{domain}"}


def main():
    idp = boto3.client("cognito-idp", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    cf = boto3.client("cloudfront")  # CloudFront is a global service
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    cfg = setup_cognito(idp)
    cfg.update(setup_hosting(s3, cf, account_id))
    CONFIG_OUT.write_text(json.dumps(cfg, indent=2))
    print(f"\nwrote {CONFIG_OUT}")
    print(f"\nCloudFront URL (allow ~5-15 min for first deploy): {cfg['cloudfront_url']}")
    print("Next: ./deploy_frontend.sh  (uploads the static files + invalidates the cache)")


if __name__ == "__main__":
    main()
