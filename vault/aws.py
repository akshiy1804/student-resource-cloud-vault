import logging
import re
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import (BotoCoreError, ClientError, ConnectTimeoutError,
                                 EndpointConnectionError, NoCredentialsError)

from . import schema

log = logging.getLogger(__name__)


class VaultError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def translate(exc):
    """Convert boto errors into friendly, non-leaky VaultErrors."""
    if isinstance(exc, VaultError):
        return exc
    if isinstance(exc, NoCredentialsError):
        return VaultError(503, "no_credentials", "AWS credentials are not configured. Set them in your shell (see README).")
    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError)):
        return VaultError(503, "aws_unreachable", "AWS is unreachable right now. Check your network and try again.")
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"ExpiredToken", "ExpiredTokenException", "RequestExpired", "InvalidClientTokenId",
                    "UnrecognizedClientException", "InvalidToken", "AuthFailure", "SignatureDoesNotMatch"}:
            return VaultError(401, "credentials_expired", "AWS credentials expired or invalid. Refresh them from the AWS Academy lab and restart the app.")
        if code in {"AccessDenied", "AccessDeniedException", "403"}:
            return VaultError(403, "access_denied", "The current AWS role is not allowed to perform this action.")
        if code in {"NoSuchKey", "404", "NotFound", "NoSuchBucket"}:
            return VaultError(404, "object_missing", "The file for this resource was not found in storage.")
        if code == "ValidationException":
            return VaultError(500, "schema_mismatch", "DynamoDB rejected the item: it does not fit the table's key schema.")
        if code == "ResourceNotFoundException":
            return VaultError(503, "table_missing", "The DynamoDB table was not found in this region.")
        if code in {"ThrottlingException", "ProvisionedThroughputExceededException", "SlowDown"}:
            return VaultError(503, "throttled", "AWS is throttling requests. Try again shortly.")
    if isinstance(exc, BotoCoreError):
        return VaultError(503, "aws_error", "An AWS service is unavailable. Try again shortly.")
    return VaultError(500, "internal_error", "Unexpected server error.")


class Vault:
    def __init__(self, cfg):
        self.cfg = cfg
        bc = BotoConfig(region_name=cfg.region, signature_version="s3v4",
                        retries={"max_attempts": 2, "mode": "standard"},
                        connect_timeout=5, read_timeout=10)
        self._session = boto3.session.Session(region_name=cfg.region)
        self.ddb = self._session.client("dynamodb", config=bc)
        self.table = self._session.resource("dynamodb", config=bc).Table(cfg.table)
        self.s3 = self._session.client("s3", config=bc)
        self.sns = self._session.client("sns", config=bc)
        self.sts = self._session.client("sts", config=bc)
        self._key = "unset"  # (name, type, has_range_key) or None when describe_table is denied
        self._topic_arn = cfg.topic_arn or None
        if self._session.get_credentials() is None:
            log.warning("No AWS credentials found in this process. Set them in the SAME PowerShell window "
                        "that starts the app (see README), then restart.")

    # ---- DynamoDB -------------------------------------------------------
    def _key_info(self):
        if self._key == "unset":
            try:
                t = self.ddb.describe_table(TableName=self.cfg.table)["Table"]
                hash_name = next(k["AttributeName"] for k in t["KeySchema"] if k["KeyType"] == "HASH")
                types = {a["AttributeName"]: a["AttributeType"] for a in t["AttributeDefinitions"]}
                self._key = (hash_name, types.get(hash_name), any(k["KeyType"] == "RANGE" for k in t["KeySchema"]))
            except Exception:  # describe may be denied; fall back to detection
                self._key = None
        return self._key

    def _partition_key(self):
        k = self._key_info()
        return k[0] if k else None

    def _scan(self):
        items, kw = [], {}
        try:
            while True:
                r = self.table.scan(**kw)
                items += r.get("Items", [])
                if "LastEvaluatedKey" not in r:
                    return items
                kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        except Exception as e:
            raise translate(e)

    def list_resources(self):
        raw = self._scan()
        amap = schema.attribute_map(raw, self.cfg.overrides, self._partition_key())
        rows = [schema.normalize(i, amap, self.cfg.key_prefix) for i in raw]
        rows.sort(key=lambda r: (str(r["category"] or ""), r["title"].lower()))
        return rows, amap

    def get_resource(self, rid):
        if not rid or len(rid) > 512 or re.search(r"[\x00-\x1f\x7f]", rid):
            raise VaultError(400, "invalid_id", "Invalid resource identifier.")
        for r in self.list_resources()[0]:
            if r["id"] == rid:
                return r
        raise VaultError(404, "not_found", "Resource not found.")

    # ---- S3 -------------------------------------------------------------
    def presign(self, rid):
        r = self.get_resource(rid)
        if r["_s3_error"]:
            raise VaultError(422, "malformed_s3_reference", f"This record's file reference is invalid: {r['_s3_error']}.")
        if not r["_s3_key"]:
            raise VaultError(404, "no_file", "This record has no file attached.")
        bucket = self.cfg.bucket or r["_s3_bucket"]
        if not bucket:
            raise VaultError(503, "bucket_not_configured", "S3_BUCKET is not configured on the server.")
        if r["_s3_bucket"] and r["_s3_bucket"] != bucket:
            raise VaultError(409, "bucket_mismatch", "This record points at a different bucket than the one configured.")
        try:
            self.s3.head_object(Bucket=bucket, Key=r["_s3_key"])
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in {"403", "AccessDenied"}:
                raise VaultError(403, "access_denied", "Access denied for this file. The role may lack s3:GetObject, "
                                 "or the file is missing and the role lacks s3:ListBucket.")
            raise translate(e)
        except Exception as e:
            raise translate(e)
        name = re.sub(r'[^\w.\- ]', "_", r["filename"] or "download")
        try:
            url = self.s3.generate_presigned_url(
                "get_object", ExpiresIn=self.cfg.presign_seconds,
                Params={"Bucket": bucket, "Key": r["_s3_key"],
                        "ResponseContentDisposition": f'attachment; filename="{name}"'})
        except Exception as e:
            raise translate(e)
        return {"url": url, "expires_in": self.cfg.presign_seconds, "filename": r["filename"]}

    # ---- Upload + SNS ---------------------------------------------------
    def add_resource(self, fileobj, filename, content_type, meta):
        """S3 put -> DynamoDB put. If the DynamoDB write fails, the new S3 object is deleted."""
        if not self.cfg.bucket:
            raise VaultError(503, "bucket_not_configured", "S3_BUCKET is not configured on the server.")
        ki = self._key_info()
        if ki and (ki[1] != "S" or ki[2]):
            raise VaultError(501, "unsupported_key_schema", "Uploads need a string partition key and no sort key; this table differs.")
        raw = self._scan()
        amap = schema.attribute_map(raw, self.cfg.overrides, self._partition_key())
        raw_sample = raw[0] if raw else {}
        rid = uuid.uuid4().hex
        key = f"{self.cfg.key_prefix}{rid[:8]}-{filename}"
        name = lambda c, d: amap.get(c, d)
        key_attr = name("s3_key", "s3_key")
        existing = raw_sample.get(key_attr)  # match the table's existing style (key vs s3:// URI)
        item = {name("id", (ki[0] if ki else "ResourceID")): rid,
                name("title", "title"): meta["title"], name("filename", "filename"): filename,
                key_attr: f"s3://{self.cfg.bucket}/{key}" if isinstance(existing, str) and existing.startswith("s3://") else key,
                "uploaded_at": datetime.now(timezone.utc).isoformat()}
        for c in ("subject", "category", "description"):
            if meta.get(c):
                item[name(c, c)] = meta[c]
        try:
            self.s3.upload_fileobj(fileobj, self.cfg.bucket, key, ExtraArgs={"ContentType": content_type})
        except Exception as e:
            raise translate(e)
        try:
            self.table.put_item(Item=item)
        except Exception as e:
            err = translate(e)
            try:
                self.s3.delete_object(Bucket=self.cfg.bucket, Key=key)
            except Exception:
                log.error("DynamoDB write failed AND cleanup failed; orphaned S3 object: %s", key)
                err.message += " The uploaded file could not be removed automatically; ask an admin to check the bucket."
            raise err
        return rid, self.notify(meta["title"], meta.get("category"))

    def _topic(self):
        if self._topic_arn:
            return self._topic_arn
        token = None
        try:
            while True:
                r = self.sns.list_topics(**({"NextToken": token} if token else {}))
                for t in r["Topics"]:
                    if t["TopicArn"].rsplit(":", 1)[-1] == self.cfg.topic_name:
                        self._topic_arn = t["TopicArn"]
                        return self._topic_arn
                token = r.get("NextToken")
                if not token:
                    raise VaultError(404, "topic_missing", "SNS topic not found.")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in {"AccessDenied", "AccessDeniedException", "AuthorizationError"}:
                raise
        # ListTopics denied: derive the ARN of the existing topic from the account id (nothing is created)
        acct = self.sts.get_caller_identity()["Account"]
        self._topic_arn = f"arn:aws:sns:{self.cfg.region}:{acct}:{self.cfg.topic_name}"
        return self._topic_arn

    def notify(self, title, category):
        """Best-effort. Returns an accurate status; 'published' means SNS accepted it, not that email was delivered."""
        try:
            r = self.sns.publish(TopicArn=self._topic(), Subject="New resource added to Student Resource Cloud Vault",
                                 Message=f"New resource: {title}\nCategory: {category or 'n/a'}")
            return {"status": "published", "message_id": r.get("MessageId")}
        except Exception as e:
            return {"status": "failed", "reason": translate(e).code}

    def check(self):
        out = {}
        for name, fn in (("dynamodb", lambda: self.ddb.describe_table(TableName=self.cfg.table)),
                         ("s3", lambda: self.s3.head_bucket(Bucket=self.cfg.bucket)),
                         ("sns", self._topic)):
            try:
                fn(); out[name] = "ok"
            except Exception as e:
                out[name] = translate(e).code
        return out
