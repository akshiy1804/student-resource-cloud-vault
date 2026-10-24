"""Run against moto-mocked AWS. These verify code paths, NOT your real AWS account."""
import io
import os

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from app import create_app
from vault.config import Config

BUCKET = "test-vault-bucket"


@pytest.fixture
def env(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


@pytest.fixture
def aws(env):
    with mock_aws():
        ddb = boto3.client("dynamodb")
        ddb.create_table(TableName="StudentResources", BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "ResourceID", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "ResourceID", "AttributeType": "S"}])
        t = boto3.resource("dynamodb").Table("StudentResources")
        t.put_item(Item={"ResourceID": "r1", "Title": "DSA Notes", "Category": "DSA", "Subject": "Algorithms", "FileName": "dsa.pdf", "S3Key": "notes/dsa.pdf"})
        t.put_item(Item={"ResourceID": "r2", "Title": "DBMS Notes", "Category": "DBMS", "Subject": "Databases", "FileName": "dbms.pdf", "S3Key": "notes/dbms.pdf"})
        t.put_item(Item={"ResourceID": "r3", "Title": "Lab Manual", "Category": "Lab", "FileName": "lab.pdf", "S3Key": "notes/missing.pdf"})
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_object(Bucket=BUCKET, Key="notes/dsa.pdf", Body=b"x")
        s3.put_object(Bucket=BUCKET, Key="notes/dbms.pdf", Body=b"x")
        boto3.client("sns").create_topic(Name="StudentResourceVaultNotifications")
        yield


def client(**over):
    cfg = Config(**{"bucket": BUCKET, "admin_token": "", **over})
    app = create_app(cfg)
    app.testing = True
    return app.test_client(), app


def test_health_does_not_need_aws():
    c, _ = client()
    assert c.get("/health").get_json()["status"] == "ok"


def test_deep_health(aws):
    c, _ = client()
    r = c.get("/health?deep=1")
    assert r.status_code == 200 and r.get_json()["aws"] == {"dynamodb": "ok", "s3": "ok", "sns": "ok"}


def test_list_search_filter(aws):
    c, _ = client()
    d = c.get("/api/resources").get_json()
    assert d["source"] == "aws" and d["count"] == 3 and set(d["categories"]) == {"DSA", "DBMS", "Lab"}
    assert all("_s3_key" not in r for r in d["resources"])
    assert c.get("/api/resources?q=algorithms").get_json()["count"] == 1
    assert c.get("/api/resources?q=dbms.pdf").get_json()["count"] == 1
    assert c.get("/api/resources?category=Lab").get_json()["count"] == 1


def test_download_presigned(aws):
    c, _ = client()
    d = c.get("/api/download?id=r1").get_json()
    assert "notes/dsa.pdf" in d["url"] and "X-Amz-Signature" in d["url"] and d["expires_in"] == 300


def test_download_errors(aws):
    c, _ = client()
    assert c.get("/api/download?id=r3").status_code == 404      # S3 object missing
    assert c.get("/api/download?id=nope").status_code == 404    # unknown id
    assert c.get("/api/download?id=").status_code == 400  # empty id


def test_no_bucket_configured(aws):
    c, _ = client(bucket="")
    assert c.get("/api/download?id=r1").get_json()["error"] == "bucket_not_configured"


def test_expired_credentials_mapped(aws, monkeypatch):
    c, app = client()
    err = ClientError({"Error": {"Code": "ExpiredToken", "Message": "x"}}, "Scan")
    monkeypatch.setattr(app.vault.table, "scan", lambda **k: (_ for _ in ()).throw(err))
    r = c.get("/api/resources")
    assert r.status_code == 401 and r.get_json()["error"] == "credentials_expired"


def test_access_denied_mapped(aws, monkeypatch):
    c, app = client()
    err = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "Scan")
    monkeypatch.setattr(app.vault.table, "scan", lambda **k: (_ for _ in ()).throw(err))
    assert c.get("/api/resources").status_code == 403


def test_upload_disabled_and_auth(aws):
    c, _ = client()
    assert c.post("/api/resources").status_code == 403
    c, _ = client(admin_token="secret")
    assert c.post("/api/resources", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_upload_end_to_end(aws):
    c, app = client(admin_token="secret")
    data = {"title": "New Notes", "category": "DSA", "subject": "Graphs", "file": (io.BytesIO(b"%PDF-1.4 hello"), "graphs.pdf")}
    r = c.post("/api/resources", data=data, headers={"X-Admin-Token": "secret"}, content_type="multipart/form-data")
    assert r.status_code == 201 and r.get_json()["notification"]["status"] == "published"
    d = c.get("/api/resources").get_json()
    new = next(x for x in d["resources"] if x["title"] == "New Notes")
    assert c.get(f"/api/download?id={new['id']}").status_code == 200
    bad = c.post("/api/resources", data={"title": "x", "file": (io.BytesIO(b"x"), "evil.exe")},
                 headers={"X-Admin-Token": "secret"}, content_type="multipart/form-data")
    assert bad.status_code == 400


def test_sns_failure_does_not_fail_upload(env):
    with mock_aws():
        boto3.client("dynamodb").create_table(TableName="StudentResources", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}], AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}])
        boto3.client("s3").create_bucket(Bucket=BUCKET)  # no SNS topic exists
        c, _ = client(admin_token="s")
        r = c.post("/api/resources", data={"title": "T", "file": (io.BytesIO(b"%PDF-x"), "a.pdf")},
                   headers={"X-Admin-Token": "s"}, content_type="multipart/form-data")
        assert r.status_code == 201 and r.get_json()["notification"]["status"] == "failed" and "NOT sent" in r.get_json()["message"]


def test_unknown_schema_and_hash_ids(env):
    with mock_aws():
        boto3.client("dynamodb").create_table(TableName="StudentResources", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}], AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "N"}])
        boto3.resource("dynamodb").Table("StudentResources").put_item(Item={"pk": 7, "name": "X", "file": "s3://b/a/x.pdf", "weird": "keep"})
        c, _ = client()
        r = c.get("/api/resources").get_json()["resources"][0]
        assert r["id"] == "7" and r["title"] == "X" and r["filename"] == "x.pdf" and r["extra"] == {"weird": "keep"}


def test_demo_mode_is_labelled(env):
    c, _ = client(demo_mode=True)
    d = c.get("/api/resources").get_json()
    assert d["source"] == "demo" and all(r["title"].startswith("[DEMO]") for r in d["resources"])
    assert c.get("/api/download?id=demo-1").status_code == 400


def test_index_page_renders():
    c, _ = client()
    assert b"Student Resource Cloud Vault" in c.get("/").data


# ---------------- additional coverage ----------------
from botocore.exceptions import NoCredentialsError  # noqa: E402


def _up(c, data, name, token="secret", ctype=None, title="T"):
    return c.post("/api/resources", data={"title": title, "file": (io.BytesIO(data), name, ctype)} if ctype else
                  {"title": title, "file": (io.BytesIO(data), name)},
                  headers={"X-Admin-Token": token}, content_type="multipart/form-data")


def test_pagination_collects_all_pages(aws, monkeypatch):
    c, app = client()
    pages = [{"Items": [{"ResourceID": "p1", "Title": "A"}], "LastEvaluatedKey": {"ResourceID": "p1"}},
             {"Items": [{"ResourceID": "p2", "Title": "B"}]}]
    calls = []
    monkeypatch.setattr(app.vault.table, "scan", lambda **k: (calls.append(k), pages[len(calls) - 1])[1])
    assert c.get("/api/resources").get_json()["count"] == 2 and "ExclusiveStartKey" in calls[1]


def test_empty_table(env):
    with mock_aws():
        boto3.client("dynamodb").create_table(TableName="StudentResources", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}], AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}])
        c, _ = client()
        d = c.get("/api/resources").get_json()
        assert d["count"] == 0 and d["source"] == "aws"


def test_aws_failure_is_error_not_fake_data(aws, monkeypatch):
    c, app = client()
    monkeypatch.setattr(app.vault.table, "scan", lambda **k: (_ for _ in ()).throw(NoCredentialsError()))
    r = c.get("/api/resources")
    assert r.status_code == 503 and r.get_json()["error"] == "no_credentials" and "resources" not in r.get_json()


def test_original_id_with_special_chars_roundtrips(env):
    with mock_aws():
        boto3.client("dynamodb").create_table(TableName="StudentResources", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "ResourceID", "KeyType": "HASH"}], AttributeDefinitions=[{"AttributeName": "ResourceID", "AttributeType": "S"}])
        boto3.resource("dynamodb").Table("StudentResources").put_item(
            Item={"ResourceID": "DSA/unit#1 v2", "Title": "X", "S3Key": f"s3://{BUCKET}/a.pdf"})
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        boto3.client("s3").put_object(Bucket=BUCKET, Key="a.pdf", Body=b"x")
        c, _ = client()
        rid = c.get("/api/resources").get_json()["resources"][0]["id"]
        assert rid == "DSA/unit#1 v2"
        from urllib.parse import quote
        assert c.get("/api/download?id=" + quote(rid, safe="")).status_code == 200


def test_bucket_mismatch_and_malformed_uri(env):
    with mock_aws():
        boto3.client("dynamodb").create_table(TableName="StudentResources", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}], AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}])
        t = boto3.resource("dynamodb").Table("StudentResources")
        t.put_item(Item={"id": "m", "s3": "s3://other-bucket/a.pdf", "key": "s3://other-bucket/a.pdf"})
        t.put_item(Item={"id": "bad", "key": "s3://nokey"})
        c, _ = client()
        assert c.get("/api/download?id=m").get_json()["error"] == "bucket_mismatch"
        assert c.get("/api/download?id=bad").get_json()["error"] == "malformed_s3_reference"


def test_s3_access_denied_on_head(aws, monkeypatch):
    c, app = client()
    err = ClientError({"Error": {"Code": "403", "Message": "x"}}, "HeadObject")
    monkeypatch.setattr(app.vault.s3, "head_object", lambda **k: (_ for _ in ()).throw(err))
    assert c.get("/api/download?id=r1").status_code == 403


def test_upload_rejects_bad_content(aws):
    c, _ = client(admin_token="secret")
    assert _up(c, b"MZ\x90 exe", "evil.pdf").status_code == 400            # contents not a PDF
    assert _up(c, b"%PDF-1", "a.pdf", ctype="text/html").status_code == 400  # declared type mismatch
    assert _up(c, b"", "a.pdf").status_code == 400                           # empty
    assert _up(c, b"%PDF-1", "a.exe").status_code == 400                     # extension
    assert _up(c, b"%PDF-1", "a.pdf", title="  ").status_code == 400         # metadata
    assert _up(c, b"\x00\x01", "n.txt").status_code == 400                   # binary text
    assert len(boto3.client("s3").list_objects_v2(Bucket=BUCKET).get("Contents", [])) == 2  # nothing leaked into S3


def test_upload_too_large(aws):
    c, app = client(admin_token="secret", max_upload_mb=1)
    app.config["MAX_CONTENT_LENGTH"] = 1024
    assert _up(c, b"%PDF-" + b"0" * 5000, "a.pdf").status_code == 413


def test_non_ascii_token_does_not_crash(aws):
    c, _ = client(admin_token="secret")
    assert _up(c, b"%PDF-1", "a.pdf", token="sécrèt").status_code == 401


def test_dynamodb_write_failure_cleans_up_s3(aws, monkeypatch):
    c, app = client(admin_token="secret")
    err = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "PutItem")
    monkeypatch.setattr(app.vault.table, "put_item", lambda **k: (_ for _ in ()).throw(err))
    r = _up(c, b"%PDF-1", "a.pdf")
    assert r.status_code == 503
    keys = [o["Key"] for o in boto3.client("s3").list_objects_v2(Bucket=BUCKET).get("Contents", [])]
    assert sorted(keys) == ["notes/dbms.pdf", "notes/dsa.pdf"]  # new object removed


def test_cleanup_failure_is_reported(aws, monkeypatch):
    c, app = client(admin_token="secret")
    monkeypatch.setattr(app.vault.table, "put_item", lambda **k: (_ for _ in ()).throw(ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "PutItem")))
    monkeypatch.setattr(app.vault.s3, "delete_object", lambda **k: (_ for _ in ()).throw(ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "DeleteObject")))
    r = _up(c, b"%PDF-1", "a.pdf")
    assert r.status_code == 403 and "could not be removed" in r.get_json()["message"]


def test_upload_record_consistent_with_s3(aws):
    c, app = client(admin_token="secret")
    assert _up(c, b"%PDF-1 data", "Unit 1.pdf", title="Unit 1").status_code == 201
    new = next(r for r in app.vault.list_resources()[0] if r["title"] == "Unit 1")
    head = boto3.client("s3").head_object(Bucket=BUCKET, Key=new["_s3_key"])
    assert head["ContentType"] == "application/pdf" and new["filename"] == "Unit_1.pdf"
    assert "key" not in _up(c, b"%PDF-1", "b.pdf").get_json()  # infrastructure details not leaked


def test_upload_matches_existing_s3_uri_style(env):
    with mock_aws():
        boto3.client("dynamodb").create_table(TableName="StudentResources", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}], AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}])
        boto3.resource("dynamodb").Table("StudentResources").put_item(Item={"id": "1", "title": "Old", "s3_uri": f"s3://{BUCKET}/old.pdf"})
        boto3.client("s3").create_bucket(Bucket=BUCKET); boto3.client("sns").create_topic(Name="StudentResourceVaultNotifications")
        c, app = client(admin_token="s")
        assert _up(c, b"%PDF-1", "n.pdf", token="s").status_code == 201
        new = next(r for r in app.vault.list_resources()[0] if r["id"] != "1")
        assert new["_s3_bucket"] == BUCKET and new["downloadable"]


def test_upload_refuses_numeric_key_table(env):
    with mock_aws():
        boto3.client("dynamodb").create_table(TableName="StudentResources", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}], AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "N"}])
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        c, _ = client(admin_token="s")
        r = _up(c, b"%PDF-1", "n.pdf", token="s")
        assert r.status_code == 501 and boto3.client("s3").list_objects_v2(Bucket=BUCKET).get("KeyCount") == 0


def test_sns_topic_discovery_falls_back_when_list_denied(aws, monkeypatch):
    c, app = client(admin_token="secret")
    deny = ClientError({"Error": {"Code": "AuthorizationError", "Message": "x"}}, "ListTopics")
    monkeypatch.setattr(app.vault.sns, "list_topics", lambda **k: (_ for _ in ()).throw(deny))
    assert _up(c, b"%PDF-1", "a.pdf").get_json()["notification"]["status"] == "published"


def test_sns_explicit_arn_used(aws):
    arn = boto3.client("sns").list_topics()["Topics"][0]["TopicArn"]
    c, _ = client(admin_token="secret", topic_arn=arn)
    assert _up(c, b"%PDF-1", "a.pdf").get_json()["notification"]["status"] == "published"


def test_demo_mode_hides_upload_and_aws(env):
    c, _ = client(demo_mode=True, admin_token="secret")
    assert c.get("/api/config").get_json()["upload_enabled"] is False
    assert c.post("/api/resources", headers={"X-Admin-Token": "secret"}).status_code == 403
    assert c.get("/api/resource?id=demo-2").get_json()["title"].startswith("[DEMO]")


def test_production_mode_never_returns_demo(aws):
    c, _ = client()
    d = c.get("/api/resources").get_json()
    assert d["source"] == "aws" and not any(r["id"].startswith("demo") for r in d["resources"])


def test_static_and_template_routes():
    c, _ = client()
    html = c.get("/").data.decode()
    assert "style.css" in html and "app.js" in html and 'id="results"' in html
    assert c.get("/static/app.js").status_code == 200 and c.get("/static/style.css").status_code == 200
    assert b"/api/download?id=" in c.get("/static/app.js").data
    assert c.get("/nope").status_code == 404 and c.get("/nope").is_json
    assert not any(k in html + c.get("/static/app.js").data.decode() for k in ("AWS_SECRET", "SessionToken", "amazonaws"))


def test_inspect_script_runs_against_mock(aws, monkeypatch, capsys):
    import runpy, sys
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setattr(sys, "argv", ["inspect_aws.py"])
    with pytest.raises(SystemExit) as ex:
        runpy.run_path("scripts/inspect_aws.py", run_name="__main__")
    out = capsys.readouterr().out
    assert ex.value.code == 1                                     # r3 deliberately points at a missing object
    assert "r1: OK" in out and "r3: MISSING" in out and "no message sent" in out
    assert "testing" not in out                                   # credential values never printed
    assert len(boto3.client("sns").list_topics()["Topics"]) == 1  # read-only: nothing created


def _real_shape(filename_for_1="DBMS-Notes.pdf"):
    """Mirrors the user's actual table: ResourceID, FileName, Folder, Subject, Description."""
    boto3.client("dynamodb").create_table(TableName="StudentResources", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "ResourceID", "KeyType": "HASH"}], AttributeDefinitions=[{"AttributeName": "ResourceID", "AttributeType": "S"}])
    boto3.resource("dynamodb").Table("StudentResources").put_item(Item={
        "ResourceID": "RES001", "FileName": filename_for_1, "Folder": "DBMS-Notes", "Subject": "DBMS", "Description": "Database management study notes"})
    s3 = boto3.client("s3"); s3.create_bucket(Bucket=BUCKET)
    s3.put_object(Bucket=BUCKET, Key="DBMS-Notes/", Body=b"")
    s3.put_object(Bucket=BUCKET, Key="DBMS-Notes/DBMS NOTES.pdf", Body=b"%PDF-1")


def test_real_schema_display_and_filter(env):
    with mock_aws():
        _real_shape()
        c, _ = client()
        d = c.get("/api/resources").get_json()
        r = d["resources"][0]
        assert (r["id"], r["title"], r["category"], r["subject"]) == ("RES001", "DBMS Notes", "DBMS", "DBMS")
        assert d["categories"] == ["DBMS"] and c.get("/api/resources?category=DBMS").get_json()["count"] == 1


def test_real_schema_wrong_filename_is_404_never_guessed(env):
    with mock_aws():
        _real_shape("DBMS-Notes.pdf")  # record name does not match the object in S3
        c, _ = client()
        r = c.get("/api/download?id=RES001")
        assert r.status_code == 404 and r.get_json()["error"] == "object_missing"


def test_real_schema_matching_filename_downloads_key_with_space(env):
    with mock_aws():
        _real_shape("DBMS NOTES.pdf")
        c, _ = client()
        d = c.get("/api/download?id=RES001").get_json()
        assert "DBMS%20NOTES.pdf" in d["url"] and d["filename"] == "DBMS NOTES.pdf"


def _run_fix(monkeypatch, capsys, *args):
    import runpy, sys
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setattr(sys, "argv", ["fix_filenames.py", *args])
    try:
        runpy.run_path("scripts/fix_filenames.py", run_name="__main__")
    except SystemExit as e:
        assert not e.code, e.code
    return capsys.readouterr().out


def test_fix_filenames_dry_run_then_apply(env, monkeypatch, capsys):
    with mock_aws():
        _real_shape("DBMS-Notes.pdf")
        t = boto3.resource("dynamodb").Table("StudentResources")
        out = _run_fix(monkeypatch, capsys)  # dry run
        assert "WOULD SET" in out and "DBMS NOTES.pdf" in out
        assert t.get_item(Key={"ResourceID": "RES001"})["Item"]["FileName"] == "DBMS-Notes.pdf"  # unchanged
        out = _run_fix(monkeypatch, capsys, "--apply")
        item = t.get_item(Key={"ResourceID": "RES001"})["Item"]
        assert item["FileName"] == "DBMS NOTES.pdf" and item["Folder"] == "DBMS-Notes" and item["Subject"] == "DBMS"
        assert "file exists" in _run_fix(monkeypatch, capsys)  # idempotent


def test_fix_filenames_skips_ambiguous_folder(env, monkeypatch, capsys):
    with mock_aws():
        _real_shape("DBMS-Notes.pdf")
        boto3.client("s3").put_object(Bucket=BUCKET, Key="DBMS-Notes/second.pdf", Body=b"%PDF-1")
        out = _run_fix(monkeypatch, capsys, "--apply")
        assert "SKIPPED" in out and boto3.resource("dynamodb").Table("StudentResources").get_item(
            Key={"ResourceID": "RES001"})["Item"]["FileName"] == "DBMS-Notes.pdf"
