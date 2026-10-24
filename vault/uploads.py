"""Upload validation: extension, declared content type, magic bytes, size, metadata."""
import re

from werkzeug.utils import secure_filename

from .aws import VaultError

_DOCX = "application/vnd.openxmlformats-officedocument."
TYPES = {  # extension -> (canonical content type, other accepted client-declared types)
    "pdf": ("application/pdf", set()),
    "doc": ("application/msword", set()),
    "docx": (_DOCX + "wordprocessingml.document", set()),
    "ppt": ("application/vnd.ms-powerpoint", set()),
    "pptx": (_DOCX + "presentationml.presentation", set()),
    "xls": ("application/vnd.ms-excel", set()),
    "xlsx": (_DOCX + "spreadsheetml.sheet", set()),
    "txt": ("text/plain", set()),
    "md": ("text/markdown", {"text/plain", "text/x-markdown"}),
    "zip": ("application/zip", {"application/x-zip-compressed"}),
    "png": ("image/png", set()),
    "jpg": ("image/jpeg", set()),
    "jpeg": ("image/jpeg", set()),
}
ALLOWED = sorted(TYPES)
_MAGIC = {"pdf": [b"%PDF-"], "png": [b"\x89PNG\r\n\x1a\n"], "jpg": [b"\xff\xd8\xff"], "jpeg": [b"\xff\xd8\xff"],
          "zip": [b"PK\x03\x04", b"PK\x05\x06"], "docx": [b"PK\x03\x04"], "pptx": [b"PK\x03\x04"], "xlsx": [b"PK\x03\x04"],
          "doc": [b"\xd0\xcf\x11\xe0"], "ppt": [b"\xd0\xcf\x11\xe0"], "xls": [b"\xd0\xcf\x11\xe0"]}
_CLEAN = re.compile(r"[\x00-\x1f\x7f]")


def validate_file(storage):
    """Return (safe_filename, content_type). Leaves the stream rewound."""
    name = secure_filename(storage.filename or "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in TYPES:
        raise VaultError(400, "bad_file_type", f"Unsupported file type. Allowed: {', '.join(ALLOWED)}.")
    canonical, extra = TYPES[ext]
    declared = (storage.mimetype or "").lower()
    if declared and declared != "application/octet-stream" and declared not in {canonical} | extra:
        raise VaultError(400, "content_type_mismatch", f"Declared type {declared} does not match .{ext}.")
    head = storage.stream.read(8192)
    storage.stream.seek(0)
    if not head:
        raise VaultError(400, "empty_file", "The file is empty.")
    if ext in _MAGIC:
        if not any(head.startswith(m) for m in _MAGIC[ext]):
            raise VaultError(400, "content_mismatch", f"File contents do not look like a real .{ext} file.")
    else:  # txt / md
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as e:
            if e.start < len(head) - 4:  # ignore a multi-byte char cut at the 8 KB boundary
                raise VaultError(400, "content_mismatch", "Text files must be UTF-8.")
        if b"\x00" in head:
            raise VaultError(400, "content_mismatch", "Text files must not contain binary data.")
    return name, canonical


def clean_meta(form):
    title = _CLEAN.sub("", form.get("title", "")).strip()[:200]
    if not title:
        raise VaultError(400, "missing_fields", "A title is required.")
    meta = {"title": title}
    for k, n in (("subject", 200), ("category", 100), ("description", 500)):
        meta[k] = re.sub(r"\s+", " ", _CLEAN.sub(" ", form.get(k, ""))).strip()[:n]
    return meta
