#!/usr/bin/env python3
"""Tear down the CloudFront/S3 hosting of the front-end (superseded by the
institutional Cloudflare Pages deployment, 2026-07). Deletes the distribution,
the origin access control, and the assets bucket. KEEPS Cognito — the pool and
app client are still the app's login and the Runtime's JWT authorizer.

Idempotent: safe to re-run; skips anything already gone. The CloudFront disable
step takes 5-20 minutes to propagate — the script waits.

    AWS_PROFILE=bigarc-autodqa AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python decommission_cloudfront.py
"""
import json
import time
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
CONFIG = HERE / ".frontend_config.json"
CF_COMMENT = "autodqa-frontend"      # how setup_frontend.py tags the distribution
OAC_NAME = "autodqa-frontend-oac"


def main():
    cf = boto3.client("cloudfront")
    s3 = boto3.client("s3")
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    bucket = f"autodqa-frontend-{account_id}"

    # 1. Distribution: disable, wait for the config to propagate, delete.
    dist = next((d for d in cf.list_distributions()
                 .get("DistributionList", {}).get("Items", [])
                 if d.get("Comment") == CF_COMMENT), None)
    if dist is None:
        print("distribution: already gone")
    else:
        dist_id = dist["Id"]
        r = cf.get_distribution_config(Id=dist_id)
        cfg, etag = r["DistributionConfig"], r["ETag"]
        if cfg["Enabled"]:
            cfg["Enabled"] = False
            etag = cf.update_distribution(Id=dist_id, DistributionConfig=cfg,
                                          IfMatch=etag)["ETag"]
            print(f"distribution {dist_id}: disabled, waiting for deploy "
                  "(5-20 min)...")
        while cf.get_distribution(Id=dist_id)["Distribution"]["Status"] != "Deployed":
            time.sleep(30)
        etag = cf.get_distribution_config(Id=dist_id)["ETag"]
        cf.delete_distribution(Id=dist_id, IfMatch=etag)
        print(f"distribution {dist_id}: deleted")

    # 2. Origin access control.
    oac_id = next((o["Id"] for o in cf.list_origin_access_controls()
                   .get("OriginAccessControlList", {}).get("Items", [])
                   if o["Name"] == OAC_NAME), None)
    if oac_id is None:
        print("OAC: already gone")
    else:
        etag = cf.get_origin_access_control(Id=oac_id)["ETag"]
        cf.delete_origin_access_control(Id=oac_id, IfMatch=etag)
        print(f"OAC {oac_id}: deleted")

    # 3. Assets bucket (contents are just the static files, tracked in git).
    try:
        objs = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
        if objs:
            s3.delete_objects(Bucket=bucket,
                              Delete={"Objects": [{"Key": o["Key"]} for o in objs]})
        s3.delete_bucket(Bucket=bucket)
        print(f"bucket {bucket}: deleted")
    except s3.exceptions.NoSuchBucket:
        print("bucket: already gone")

    # 4. Drop the hosting keys from .frontend_config.json; the Cognito keys
    # remain (make_config.py and the Runtime's authorizer config read them).
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text())
        for k in ("s3_bucket", "cloudfront_distribution_id",
                  "cloudfront_domain", "cloudfront_url"):
            cfg.pop(k, None)
        CONFIG.write_text(json.dumps(cfg, indent=2))
        print(f"{CONFIG.name}: hosting keys removed (Cognito keys kept)")

    print("\ndone — hosting decommissioned; Cognito untouched")


if __name__ == "__main__":
    main()
