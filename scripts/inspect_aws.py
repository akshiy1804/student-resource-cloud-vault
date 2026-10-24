"""Read-only inspection of the EXISTING AWS resources, using the same code paths as the app.

    python scripts/inspect_aws.py                 # read-only, sends nothing
    python scripts/inspect_aws.py --publish-test  # ALSO publishes one real SNS message (emails subscribers)

Never prints access keys, secret keys or session tokens. Creates nothing.
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from vault.aws import Vault, VaultError, translate  # noqa: E402
from vault.config import Config  # noqa: E402

HELP = """
No usable AWS credentials. In the SAME PowerShell window you will run the app from:
    Start Lab > AWS Details > Show, copy the 3 values, then:
    $env:AWS_ACCESS_KEY_ID="..."; $env:AWS_SECRET_ACCESS_KEY="..."; $env:AWS_SESSION_TOKEN="..."; $env:AWS_REGION="us-east-1"
  (or copy the 'AWS CLI' block and run:  . .\\scripts\\set-credentials.ps1)
"""
failures = []
help_shown = []


def mask(email):
    if "@" not in email:
        return email
    u, d = email.split("@", 1)
    return f"{u[:1]}***@{d}"


def step(title, fn):
    print(f"\n== {title}")
    try:
        fn()
    except Exception as e:
        t = translate(e)
        failures.append(title)
        print(f"   FAILED [{t.code}]: {t.message}")
        if t.code in {"no_credentials", "credentials_expired"}:
            if not help_shown:
                help_shown.append(1)
                print(HELP)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish-test", action="store_true", help="send ONE real SNS message (explicit opt-in)")
    args = ap.parse_args()
    cfg = Config()
    v = Vault(cfg)
    print(f"Region: {cfg.region} | Table: {cfg.table} | Bucket: {cfg.bucket or '(S3_BUCKET not set)'} | Topic: {cfg.topic_name}")
    state = {}

    def ident():
        i = v.sts.get_caller_identity()
        print(f"   account {i['Account']}  principal {i['Arn']}")
    step("AWS identity (STS)", ident)

    def ddb():
        raw = v._scan()
        ki = v._key_info()
        print(f"   key schema (hash, type, has sort key): {ki}")
        print(f"   items: {len(raw)}")
        for it in raw:
            print("   -", {k: type(x).__name__ for k, x in it.items()})
        rows, amap = v.list_resources()
        state["rows"] = rows
        print(f"   detected attribute mapping: {amap}")
        print("   what the app will show:")
        for r in rows:
            print(f"   - id={r['id']!r} title={r['title']!r} subject={r['subject']!r} category={r['category']!r}\n"
                  f"     filename={r['filename']!r} description={(r['description'] or '')[:50]!r}\n"
                  f"     -> bucket={r['_s3_bucket'] or cfg.bucket!r} key={r['_s3_key']!r} problem={r['_s3_error']}")
    step(f"DynamoDB table {cfg.table}", ddb)

    def s3():
        if not cfg.bucket:
            names = [b["Name"] for b in v.s3.list_buckets()["Buckets"]]
            print(f"   S3_BUCKET is not set. Buckets visible to this role: {names}\n   Set S3_BUCKET in .env, then re-run.")
            failures.append("S3_BUCKET not set")
            return
        resp = v.s3.list_objects_v2(Bucket=cfg.bucket, MaxKeys=200)
        objs = {o["Key"]: o["Size"] for o in resp.get("Contents", [])}
        print(f"   bucket has {resp.get('KeyCount', 0)} object(s) (first 200 listed):")
        for k, sz in objs.items():
            print(f"   - {k}  ({sz} bytes, .{k.rsplit('.', 1)[-1].lower() if '.' in k else '?'})")
        print("   record -> object check (HeadObject only, nothing downloaded):")
        used = set()
        for r in state.get("rows", []):
            b, k = r["_s3_bucket"] or cfg.bucket, r["_s3_key"]
            if r["_s3_error"] or not k:
                print(f"   - {r['id']}: NO USABLE KEY ({r['_s3_error'] or 'no file attributes'})"); failures.append("record without key"); continue
            if b != cfg.bucket:
                print(f"   - {r['id']}: points at another bucket {b!r}"); failures.append("bucket mismatch"); continue
            try:
                h = v.s3.head_object(Bucket=b, Key=k)
                used.add(k)
                print(f"   - {r['id']}: OK  {k}  {h['ContentLength']} bytes  {h.get('ContentType')}")
            except Exception as e:
                print(f"   - {r['id']}: MISSING/DENIED  {k}  [{translate(e).code}]"); failures.append("object check")
        orphans = [k for k in objs if k not in used]
        if orphans:
            print(f"   objects in bucket not referenced by any record: {orphans}")
        try:
            print("   public access block:", v.s3.get_public_access_block(Bucket=cfg.bucket)["PublicAccessBlockConfiguration"])
        except Exception:
            print("   public access block: not readable with this role (cannot confirm the bucket is private)")
    step("S3 bucket", s3)

    def sns():
        arn = v._topic()
        print(f"   topic ARN: {arn}")
        subs = v.sns.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"]
        print("   subscriptions:", Counter("confirmed" if s["SubscriptionArn"].startswith("arn:") else s["SubscriptionArn"] for s in subs))
        for s in subs:
            print(f"   - {s['Protocol']} {mask(s['Endpoint'])}: {'confirmed' if s['SubscriptionArn'].startswith('arn:') else s['SubscriptionArn']}")
        if args.publish_test:
            r = v.sns.publish(TopicArn=arn, Subject="Vault test", Message="Test message from inspect_aws.py")
            print(f"   test message accepted by SNS, MessageId {r['MessageId']} (check the inbox to confirm delivery)")
        else:
            print("   (no message sent; use --publish-test to send one)")
    step("SNS topic", sns)

    print("\nRESULT:", "all checks passed" if not failures else f"{len(failures)} problem(s): {sorted(set(failures))}")
    sys.exit(1 if failures else 0)


main()
