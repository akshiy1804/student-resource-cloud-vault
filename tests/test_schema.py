import pytest

from vault.schema import attribute_map, normalize, parse_s3_ref


@pytest.mark.parametrize("val,expected", [
    ("s3://my-bucket/notes/dsa.pdf", ("my-bucket", "notes/dsa.pdf", None)),
    ("notes/dsa.pdf", (None, "notes/dsa.pdf", None)),
    ("/notes/dsa.pdf", (None, "notes/dsa.pdf", None)),
    ("https://my-bucket.s3.amazonaws.com/notes/dsa.pdf", ("my-bucket", "notes/dsa.pdf", None)),
    ("https://my-bucket.s3.us-east-1.amazonaws.com/notes/a%20b.pdf?X-Amz=1", ("my-bucket", "notes/a b.pdf", None)),
    ("https://s3.us-east-1.amazonaws.com/my-bucket/notes/dsa.pdf", ("my-bucket", "notes/dsa.pdf", None)),
    ("", (None, None, None)), (None, (None, None, None)),
])
def test_parse_ok(val, expected):
    assert parse_s3_ref(val) == expected


@pytest.mark.parametrize("val", ["s3://", "s3://bucket", "s3://bucket/", "s3://bucket/folder/",
                                 "https://example.com/file.pdf", "folder/", "a\x00b"])
def test_parse_malformed(val):
    b, k, err = parse_s3_ref(val)
    assert k is None and err


def rec(**kw):
    amap = attribute_map([kw], key_attr=None)
    return normalize(kw, amap)


@pytest.mark.parametrize("item", [
    {"ResourceID": "1", "Title": "T", "FileName": "a.pdf"},
    {"resource_id": "1", "title": "T", "filename": "a.pdf"},
    {"id": "1", "Name": "T", "File": "a.pdf"},
])
def test_attribute_variations(item):
    r = rec(**item)
    assert (r["id"], r["title"], r["filename"]) == ("1", "T", "a.pdf")


def test_s3_uri_in_key_field_not_shown_as_filename():
    r = rec(ResourceID="1", Title="T", S3Key="s3://b/notes/dsa.pdf")
    assert (r["filename"], r["_s3_bucket"], r["_s3_key"]) == ("dsa.pdf", "b", "notes/dsa.pdf")


def test_s3_uri_in_filename_field():
    r = rec(id="1", FileName="s3://b/notes/dsa.pdf")
    assert (r["filename"], r["_s3_bucket"], r["_s3_key"]) == ("dsa.pdf", "b", "notes/dsa.pdf")


def test_explicit_key_beats_filename_and_distinct():
    r = rec(id="1", FileName="Nice Name.pdf", S3Key="raw/key-123.pdf")
    assert (r["filename"], r["_s3_key"]) == ("Nice Name.pdf", "raw/key-123.pdf")


def test_folder_plus_filename():
    r = rec(id="1", Folder="DBMS/", FileName="notes.pdf")
    assert r["_s3_key"] == "DBMS/notes.pdf" and r["downloadable"]


def test_malformed_uri_not_downloadable():
    r = rec(id="1", S3Key="s3://bucket-only")
    assert not r["downloadable"] and r["_s3_error"]


def test_missing_attributes_and_hash_id():
    r = rec(foo="bar")
    assert r["id"].startswith("h-") and r["title"] == r["id"] and not r["downloadable"]


def test_override_wins():
    amap = attribute_map([{"Key": "x", "MyFile": "y"}], overrides={"s3_key": "MyFile"})
    assert amap["s3_key"] == "MyFile"
