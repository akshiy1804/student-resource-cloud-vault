"""Maps whatever attribute names exist in DynamoDB onto canonical fields, and
resolves each record to an (S3 bucket, S3 key) pair.

Attribute names are matched case/punctuation-insensitively and can be pinned
with FIELD_* environment variables.
"""
import hashlib
import json
import re
from decimal import Decimal
from urllib.parse import unquote, urlparse

CANDIDATES = {
    "id": ["resourceid", "id", "resource_id", "itemid", "fileid", "uuid"],
    "title": ["title", "name", "resourcename", "resourcetitle", "topic"],
    "subject": ["subject", "course", "coursename", "subjectname"],
    "category": ["category", "type", "resourcetype", "kind", "section"],
    "description": ["description", "desc", "summary", "details", "about"],
    "filename": ["filename", "file", "fname", "originalfilename"],
    "folder": ["folder", "foldername", "directory", "dir"],
    "s3_key": ["s3key", "key", "objectkey", "filekey", "filepath", "path", "s3path", "s3uri", "s3url", "location"],
}
_norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_VHOST = re.compile(r"^(?P<b>.+)\.s3[.-](?:[a-z0-9-]+\.)?amazonaws\.com(?:\.cn)?$")
_PATHSTYLE = re.compile(r"^s3[.-](?:[a-z0-9-]+\.)?amazonaws\.com(?:\.cn)?$")


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (set, frozenset)):
        return sorted(_plain(x) for x in v)
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def attribute_map(items, overrides=None, key_attr=None):
    """Return {canonical_field: real_attribute_name} detected from sample items."""
    attrs = {}
    for it in items:
        for a in it:
            attrs.setdefault(_norm(a), a)
    found = {c: a for c, a in (overrides or {}).items()}
    if key_attr and "id" not in found:
        found["id"] = key_attr
    for canon, names in CANDIDATES.items():
        if canon in found:
            continue
        for n in names:
            if _norm(n) in attrs:
                found[canon] = attrs[_norm(n)]
                break
    return found


def _valid_key(k):
    return bool(k) and len(k.encode()) <= 1024 and not k.endswith("/") and not _CTRL.search(k)


def parse_s3_ref(value):
    """-> (bucket|None, key|None, error|None).

    Accepts 's3://bucket/key', virtual-hosted and path-style https S3 URLs, or a
    plain key ('folder/file.pdf'). Anything malformed returns an error string
    instead of being guessed at.
    """
    if not isinstance(value, str) or not value.strip():
        return None, None, None
    v = value.strip()
    if v.lower().startswith("s3://"):
        m = re.fullmatch(r"s3://([^/\s]+)/(.+)", v, re.I)
        if not m or not _valid_key(m.group(2)):
            return None, None, "malformed s3:// URI (expected s3://bucket/key)"
        return m.group(1), m.group(2), None
    if re.match(r"^https?://", v, re.I):
        u = urlparse(v)
        host, path = (u.hostname or "").lower(), unquote(u.path.lstrip("/"))
        vm = _VHOST.match(host)
        if vm and _valid_key(path):
            return vm.group("b"), path, None
        if _PATHSTYLE.match(host) and "/" in path:
            b, k = path.split("/", 1)
            if _valid_key(k):
                return b, k, None
        return None, None, "URL is not a recognisable S3 object URL"
    key = v.lstrip("/")
    if not _valid_key(key):
        return None, None, "malformed S3 key"
    return None, key, None


def normalize(item, amap, key_prefix=""):
    raw = _plain(item)
    get = lambda c: raw.get(amap[c]) if c in amap and raw.get(amap[c]) not in (None, "") else None
    rid = get("id")
    if rid is None:
        rid = "h-" + hashlib.sha1(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:12]

    bucket, key, err = parse_s3_ref(get("s3_key"))
    filename = get("filename")
    if isinstance(filename, str):
        filename = filename.strip()
        if "/" in filename:  # filename field actually holds a path/URI
            if not key and not err:
                bucket, key, err = parse_s3_ref(filename)
            filename = filename.rstrip("/").rsplit("/", 1)[-1]
    filename = filename or (key.rsplit("/", 1)[-1] if key else None)
    if not key and not err and filename:
        folder = get("folder")
        if isinstance(folder, str) and folder.strip().strip("/"):
            key = f"{folder.strip().strip('/')}/{filename}"
        else:
            key = f"{key_prefix}{filename}"
    used = {amap[c] for c in amap if c != "folder"}
    title = get("title")
    if not title and filename:  # no title attribute: show the filename without extension, e.g. "DBMS-Notes.pdf" -> "DBMS Notes"
        title = re.sub(r"[-_]+", " ", filename.rsplit(".", 1)[0]).strip() or filename
    # No category attribute in the table: use Subject for the filter chips (display only; nothing is written back)
    category = get("category") if "category" in amap else get("subject")
    return {
        "id": str(rid),
        "title": str(title or rid),
        "subject": get("subject"),
        "category": category,
        "description": get("description"),
        "filename": filename,
        "downloadable": bool(key) and not err,
        "extra": {k: v for k, v in raw.items() if k not in used},
        "_s3_bucket": bucket,
        "_s3_key": key,
        "_s3_error": err,
    }


def public(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}
