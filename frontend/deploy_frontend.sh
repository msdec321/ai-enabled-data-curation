#!/usr/bin/env bash
# Build config.js, upload the static front-end to S3, and invalidate CloudFront.
# Uses boto3 (no aws CLI on this box) + the bigarc-autodqa profile.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"
export AWS_PROFILE="${AWS_PROFILE:-bigarc-autodqa}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
PY="$REPO_ROOT/.venv/bin/python"

[ -f .frontend_config.json ] || { echo "ERROR: run setup_frontend.py first"; exit 1; }

echo ">> generating config.js"
"$PY" make_config.py

echo ">> uploading static files + invalidating CloudFront"
"$PY" - <<'PYEOF'
import json, mimetypes, uuid, boto3
cfg = json.load(open(".frontend_config.json"))
bucket, dist = cfg["s3_bucket"], cfg["cloudfront_distribution_id"]

s3 = boto3.client("s3")
for name in ["index.html", "app.js", "config.js"]:
    ct = mimetypes.guess_type(name)[0] or "text/plain"
    s3.upload_file(name, bucket, name,
                   ExtraArgs={"ContentType": ct, "CacheControl": "no-cache"})
    print(f"   uploaded {name} ({ct})")

cf = boto3.client("cloudfront")
cf.create_invalidation(DistributionId=dist, InvalidationBatch={
    "CallerReference": "deploy-" + uuid.uuid4().hex,
    "Paths": {"Quantity": 1, "Items": ["/*"]}})
print(f"   invalidated distribution {dist}")
print("\nLive at:", cfg["cloudfront_url"])
PYEOF
