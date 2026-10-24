import hmac
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from vault import demo, schema
from vault.aws import Vault, VaultError, translate
from vault.config import Config
from vault.uploads import ALLOWED, clean_meta, validate_file


def create_app(cfg=None, vault=None):
    load_dotenv()
    cfg = cfg or Config()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_upload_mb * 1024 * 1024
    app.vault = vault or Vault(cfg)

    @app.errorhandler(VaultError)
    def _vault_err(e):
        return jsonify(error=e.code, message=e.message), e.status

    @app.errorhandler(413)
    def _too_big(_):
        return jsonify(error="too_large", message=f"File exceeds {cfg.max_upload_mb} MB."), 413

    @app.errorhandler(Exception)
    def _any(e):
        if hasattr(e, "code") and isinstance(e.code, int):  # werkzeug HTTPException
            return jsonify(error="http_error", message=getattr(e, "description", "Error")), e.code
        app.logger.exception("unhandled")
        t = translate(e)
        return jsonify(error=t.code, message=t.message), t.status

    @app.after_request
    def _headers(r):
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["Referrer-Policy"] = "same-origin"
        if request.path.startswith("/api"):
            r.headers["Cache-Control"] = "no-store"
        return r

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        body = {"status": "ok", "demo_mode": cfg.demo_mode}
        if request.args.get("deep") == "1":  # touches AWS; for manual checks only
            body["aws"] = app.vault.check()
            body["status"] = "ok" if all(v == "ok" for v in body["aws"].values()) else "degraded"
        return jsonify(body), 200 if body["status"] == "ok" else 503

    @app.get("/api/config")
    def api_config():
        return jsonify(demo_mode=cfg.demo_mode, upload_enabled=bool(cfg.admin_token) and not cfg.demo_mode,
                       max_upload_mb=cfg.max_upload_mb, allowed_extensions=ALLOWED)

    @app.get("/api/resources")
    def list_resources():
        if cfg.demo_mode:
            rows, source = demo.DEMO, "demo"
        else:
            rows, source = [schema.public(r) for r in app.vault.list_resources()[0]], "aws"
        cats = sorted({r["category"] for r in rows if r["category"]})
        q = request.args.get("q", "").strip().lower()[:100]
        cat = request.args.get("category", "").strip()
        if cat:
            rows = [r for r in rows if r["category"] == cat]
        if q:
            rows = [r for r in rows if q in " ".join(str(r[k] or "") for k in ("title", "subject", "category", "filename", "description")).lower()]
        return jsonify(source=source, categories=cats, count=len(rows), resources=rows)

    # The original record ID is passed as ?id= so IDs containing "/", "#" etc. survive routing unchanged.
    @app.get("/api/resource")
    def one():
        rid = request.args.get("id", "")
        if cfg.demo_mode:
            hit = next((r for r in demo.DEMO if r["id"] == rid), None)
            if not hit:
                raise VaultError(404, "not_found", "Resource not found.")
            return jsonify(hit)
        return jsonify(schema.public(app.vault.get_resource(rid)))

    @app.get("/api/download")
    def download():
        if cfg.demo_mode:
            raise VaultError(400, "demo_mode", "Demo entries have no real files.")
        return jsonify(app.vault.presign(request.args.get("id", "")))

    @app.post("/api/resources")
    def add():
        if not cfg.admin_token or cfg.demo_mode:
            raise VaultError(403, "uploads_disabled", "Uploads are disabled on this server.")
        supplied = request.headers.get("X-Admin-Token", "").encode("utf-8")
        if not hmac.compare_digest(supplied, cfg.admin_token.encode("utf-8")):
            raise VaultError(401, "unauthorized", "Invalid admin token.")
        f = request.files.get("file")
        if not f:
            raise VaultError(400, "missing_fields", "A file is required.")
        meta = clean_meta(request.form)
        name, content_type = validate_file(f)
        rid, note = app.vault.add_resource(f.stream, name, content_type, meta)
        if note["status"] == "published":
            msg = "Resource saved. Notification was published to SNS (delivery to email is handled by SNS)."
        else:
            msg = f"Resource saved, but the SNS notification was NOT sent ({note['reason']})."
        return jsonify(id=rid, notification=note, message=msg), 201

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
