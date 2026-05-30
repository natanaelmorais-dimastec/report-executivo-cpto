"""
AWS tag auditor for the Dimastec cost report tagging effort.

READ-ONLY. This script inspects existing resource tags so you can understand the
current tagging landscape BEFORE standardizing on a single `Produto` tag. It does
not create, modify, or delete anything.

What it does:
  - Lists all tagged resources via the Resource Groups Tagging API
  - Counts which tag keys exist and how often (surfaces near-duplicate keys like
    'environment' vs 'Environment' vs 'enviroment')
  - For a set of candidate "product" tag keys, shows which values appear and on
    how many resources
  - Flags resources that have NO recognizable product tag

Usage:
    python tag_audit.py --profile dimastec-mgmt --region sa-east-1
    python tag_audit.py --profile dimastec-mgmt --region sa-east-1 --output tag_audit.csv

Requirements: boto3, AWS credentials with tag:GetResources (and resourcegroupstaggingapi).
NOTE: All log messages are intentionally in English.
"""

import argparse
import csv
import logging
import sys
from collections import Counter, defaultdict

import boto3

# Tag keys that might encode the product, based on what we already saw in the
# console (mydhas-prd, mydhas-qa, etc.). Extend this list as the audit reveals more.
# Matching is case-insensitive and also checks if a key/value mentions a product name.
PRODUCT_HINT_KEYS = [
    "produto", "product", "projeto", "project",
    "name", "app", "application", "service", "sistema",
    "managed-by", "managedby", "owner",
]
PRODUCT_NAMES = ["faceum", "mydhas", "ai", "dtfaceum", "dt-faceum"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tag_audit")


def iter_resources(client):
    """Yield every tagged resource (paginated). Read-only."""
    paginator = client.get_paginator("get_resources")
    for page in paginator.paginate(ResourcesPerPage=100):
        for item in page["ResourceTagMappingList"]:
            yield item


def short_arn(arn: str) -> str:
    """Return service + resource name from an ARN for readable output."""
    parts = arn.split(":")
    service = parts[2] if len(parts) > 2 else "?"
    tail = parts[-1] if parts else arn
    return f"{service}/{tail}"


def looks_like_product(key: str, value: str) -> str | None:
    """Return the product name if a tag key/value clearly references a product."""
    blob = f"{key} {value}".lower()
    for product in PRODUCT_NAMES:
        if product in blob:
            # normalize dtfaceum -> faceum
            if "faceum" in product:
                return "Faceum"
            if "mydhas" in product:
                return "Mydhas"
            if product == "ai":
                return "AI"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit existing AWS resource tags (read-only).")
    parser.add_argument("--profile", default=None, help="AWS named profile")
    parser.add_argument("--region", default="sa-east-1", help="Region to audit")
    parser.add_argument("--output", default=None, help="Optional CSV path for the full inventory")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    client = session.client("resourcegroupstaggingapi", region_name=args.region)

    key_counter = Counter()             # how often each tag key appears
    product_value_counter = Counter()   # inferred product -> resource count
    resources = []                      # full inventory for optional CSV
    untagged_product = []               # resources with no recognizable product

    log.info("Scanning tagged resources in region %s ...", args.region)
    try:
        for item in iter_resources(client):
            arn = item["ResourceARN"]
            tags = {t["Key"]: t["Value"] for t in item.get("Tags", [])}
            for k in tags:
                key_counter[k] += 1

            # try to infer product from any tag
            inferred = None
            for k, v in tags.items():
                inferred = looks_like_product(k, v)
                if inferred:
                    break
            if inferred:
                product_value_counter[inferred] += 1
            else:
                product_value_counter["(sem produto identificado)"] += 1
                untagged_product.append(short_arn(arn))

            resources.append({
                "resource": short_arn(arn),
                "arn": arn,
                "inferred_product": inferred or "",
                "tags": "; ".join(f"{k}={v}" for k, v in tags.items()),
            })
    except Exception as exc:
        log.error("Failed to scan resources: %s", exc)
        log.error("Check that the profile has tag:GetResources permission.")
        return 1

    # --- Report: tag key consistency ---
    log.info("=== TAG KEYS FOUND (watch for near-duplicates) ===")
    for key, count in key_counter.most_common():
        log.info("  %-30s %4d resources", key, count)

    # surface likely-duplicate keys (same lowercased form, different casing/spelling)
    lowered = defaultdict(list)
    for key in key_counter:
        lowered[key.lower().replace("-", "").replace("_", "")].append(key)
    dupes = {norm: ks for norm, ks in lowered.items() if len(ks) > 1}
    if dupes:
        log.warning("=== LIKELY DUPLICATE KEYS (AWS treats these as different!) ===")
        for norm, ks in dupes.items():
            log.warning("  %s  ->  %s", norm, ", ".join(ks))

    # --- Report: product inference ---
    log.info("=== RESOURCES BY INFERRED PRODUCT ===")
    total = sum(product_value_counter.values())
    for product, count in product_value_counter.most_common():
        share = (count / total * 100) if total else 0
        log.info("  %-30s %4d  (%4.1f%%)", product, count, share)

    if untagged_product:
        log.warning("%d resources have NO recognizable product tag.", len(untagged_product))
        for r in untagged_product[:20]:
            log.warning("    - %s", r)
        if len(untagged_product) > 20:
            log.warning("    ... and %d more", len(untagged_product) - 20)

    # --- Optional CSV ---
    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["resource", "arn", "inferred_product", "tags"])
            writer.writeheader()
            writer.writerows(resources)
        log.info("Full inventory written to %s (%d resources)", args.output, len(resources))

    log.info("Done. This was read-only — nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
