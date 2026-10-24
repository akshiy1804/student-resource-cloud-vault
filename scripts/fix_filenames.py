"""One-time repair: make DynamoDB 'FileName' match the real object name in S3.

    python scripts/fix_filenames.py           # DRY RUN: shows what would change, writes nothing
    python scripts/fix_filenames.py --apply   # actually updates the FileName attribute

Rules (nothing is guessed):
  - A record is left alone if its current Folder/FileName already exists in S3.
  - Otherwise, if the record's Folder contains EXACTLY ONE file, FileName is set to that file's name.
  - If the folder has zero or several files, the record is skipped and reported.
Only the filename attribute is written. Needs dynamodb:UpdateItem (only with --apply).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from vault import schema  # noqa: E402
from vault.aws import Vault, translate  # noqa: E402
from vault.config import Config  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    args = ap.parse_args()
    cfg = Config()
    if not cfg.bucket:
        sys.exit("Set S3_BUCKET in .env first.")
    v = Vault(cfg)
    try:
        raw = v._scan()
        amap = schema.attribute_map(raw, cfg.overrides, v._partition_key())
        if not all(k in amap for k in ("id", "filename", "folder")):
            sys.exit(f"Needs id, filename and folder attributes; detected: {amap}")
        changes = 0
        for item in raw:
            rec = schema.normalize(item, amap, cfg.key_prefix)
            folder = str(item.get(amap["folder"], "")).strip().strip("/")
            label = f"{rec['id']} (folder {folder!r}, FileName {item.get(amap['filename'])!r})"
            try:
                v.s3.head_object(Bucket=cfg.bucket, Key=rec["_s3_key"])
                print(f"OK       {label}: file exists, no change")
                continue
            except Exception as e:
                if translate(e).code != "object_missing":
                    raise
            keys = []
            for page in v.s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=folder + "/"):
                keys += [o["Key"] for o in page.get("Contents", []) if not o["Key"].endswith("/")]
            if len(keys) != 1:
                print(f"SKIPPED  {label}: folder has {len(keys)} files, cannot choose safely {keys}")
                continue
            new = keys[0].rsplit("/", 1)[-1]
            print(f"{'UPDATING' if args.apply else 'WOULD SET'} {label} -> FileName {new!r}")
            changes += 1
            if args.apply:
                v.table.update_item(
                    Key={v._partition_key(): item[v._partition_key()]},
                    UpdateExpression="SET #f = :v", ConditionExpression="attribute_exists(#k)",
                    ExpressionAttributeNames={"#f": amap["filename"], "#k": v._partition_key()},
                    ExpressionAttributeValues={":v": new})
        print(f"\n{'Applied' if args.apply else 'Dry run:'} {changes} change(s)." + ("" if args.apply or not changes else " Re-run with --apply to write them."))
    except SystemExit:
        raise
    except Exception as e:
        t = translate(e)
        sys.exit(f"FAILED [{t.code}]: {t.message}")


main()
