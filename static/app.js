const $ = (id) => document.getElementById(id);
const state = { q: "", category: "" };
let timer, catsKey = null;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let order = [];
const hue = (s) => Math.max(0, order.indexOf(s)) % 6;  // each subject gets its own colour in order
const ext = (name) => (name && name.includes(".") ? name.split(".").pop().toUpperCase() : "FILE");

const SERVER_SIDE = new Set(["no_credentials", "credentials_expired", "access_denied", "aws_unreachable", "aws_error", "throttled", "table_missing", "bucket_not_configured", "internal_error"]);
const FILE_GONE = new Set(["object_missing", "no_file", "malformed_s3_reference", "bucket_mismatch", "not_found"]);

function toast(msg, err) {
  const t = $("toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 5000);
}
async function api(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(body.message || `Request failed (${r.status})`); e.code = body.error; throw e; }
  return body;
}
function render(d) {
  if (!state.category && !state.q) order = d.categories.slice();
  $("demo-note").hidden = d.source !== "demo";
  const key = d.categories.join("|");
  if (key !== catsKey) {
    catsKey = key;
    $("cats").innerHTML = d.categories.length
      ? ["", ...d.categories].map((c) => `<button type="button" class="chip" data-c="${esc(c)}" aria-pressed="${c === state.category}">${c ? esc(c) : "All subjects"}</button>`).join("") : "";
  } else {
    document.querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", b.dataset.c === state.category));
  }
  $("count").textContent = d.count ? `${d.count} resource${d.count === 1 ? "" : "s"}` : "";
  $("empty").hidden = d.count > 0;
  $("results").innerHTML = d.resources.map((r) => `
    <li class="card s${hue(r.category || r.subject)}">
      ${r.category || r.subject ? `<span class="subject">${esc(r.category || r.subject)}</span>` : ""}
      <h2>${esc(r.title)}</h2>
      ${r.description ? `<p>${esc(r.description)}</p>` : ""}
      <div class="foot">
        <span class="type">${esc(ext(r.filename))}</span>
        <button class="dl" data-id="${esc(r.id)}" ${r.downloadable && d.source === "aws" ? "" : "disabled"}>Download</button>
      </div>
    </li>`).join("");
  $("results").setAttribute("aria-busy", "false");
}
async function load() {
  $("results").setAttribute("aria-busy", "true");
  if (!$("results").children.length) $("results").innerHTML = '<li class="skeleton"></li>'.repeat(3);
  $("banner").hidden = true;
  try {
    render(await api(`/api/resources?${new URLSearchParams({ q: state.q, category: state.category })}`));
  } catch (e) {
    $("results").innerHTML = ""; $("count").textContent = ""; $("empty").hidden = true;
    $("results").setAttribute("aria-busy", "false");
    $("banner-detail").textContent = e.message; $("banner").hidden = false;
  }
}
$("q").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => { state.q = e.target.value.trim(); load(); }, 250); });
$("cats").addEventListener("click", (e) => { const b = e.target.closest(".chip"); if (b) { state.category = b.dataset.c; load(); } });
$("clear").addEventListener("click", () => { state.q = state.category = ""; $("q").value = ""; load(); });
$("retry").addEventListener("click", load);

$("results").addEventListener("click", async (e) => {
  const b = e.target.closest(".dl"); if (!b) return;
  b.disabled = true; b.textContent = "Preparing…";
  try {
    const d = await api(`/api/download?id=${encodeURIComponent(b.dataset.id)}`);
    toast("Your download is starting.");
    window.location.assign(d.url);
  } catch (err) {
    toast(FILE_GONE.has(err.code) ? "Sorry, that file isn't available right now."
      : SERVER_SIDE.has(err.code) ? "Downloads are temporarily unavailable. Please try again later." : err.message, true);
  } finally { b.disabled = false; b.textContent = "Download"; }
});

const dlg = $("share");
$("open-share").addEventListener("click", () => dlg.showModal());
$("close-share").addEventListener("click", () => dlg.close());
$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target); const code = fd.get("token"); fd.delete("token");
  if (!fd.get("category")) fd.set("category", fd.get("subject") || "");
  const btn = e.target.querySelector(".primary"); btn.disabled = true; btn.textContent = "Sharing…";
  try {
    const d = await api("/api/resources", { method: "POST", body: fd, headers: { "X-Admin-Token": code } });
    const ok = d.notification.status === "published";
    toast(ok ? "Shared! Subscribers will get an email." : `Shared, but the email update could not be sent (${d.notification.reason}).`, !ok);
    e.target.reset(); dlg.close(); catsKey = null; load();
  } catch (err) {
    toast(err.code === "unauthorized" ? "That access code isn't right." : err.message, true);
  } finally { btn.disabled = false; btn.textContent = "Share with everyone"; }
});
(async () => {
  try { const c = await api("/api/config"); $("open-share").hidden = !c.upload_enabled; } catch {}
  load();
})();
