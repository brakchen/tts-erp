"""/v2/pages/* — server-rendered HTML pages (no SPA framework).

The manual-costs page is the only one Lane E ships. It's a single
HTML response built with plain Python string templates (no Jinja
dependency to add). Form submission is JSON POST to
``/v2/reporting/manual-costs`` via a tiny inline JS handler.

Auth classification: this page is ``readonly``-equivalent for the GET
(handler does no DB writes). It calls ``/v2/reporting/missing-cost-products``
on the page itself via ``fetch()``, so the operator must hold a
readwrite or admin key to actually fill in costs.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/v2/pages", tags=["pages"])


@router.get("/manual-costs", response_class=HTMLResponse)
def manual_costs_page() -> HTMLResponse:
    """Single-page manual cost entry form.

    Renders a static HTML shell. The JS handler:
    - Reads the bearer token from localStorage (``mc_token``); the
      operator pastes it on first visit (no SPA = no place to store it
      safely otherwise).
    - Fetches ``/v2/reporting/missing-cost-products`` with the token.
    - Renders the table inline.
    - On submit, POSTs JSON to ``/v2/reporting/manual-costs`` and
      refreshes the list on success.

    The page deliberately avoids any frontend framework — the operator
    set is small (≤ hundreds of SKUs in flight) and the workflow is
    linear.
    """
    return HTMLResponse(_PAGE_HTML)


_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>tts-erp · manual cost entry</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1100px; margin: 24px auto; padding: 0 16px; color: #1f2328; }}
  h1 {{ font-size: 18px; margin-bottom: 8px; }}
  .auth {{ display: flex; gap: 8px; margin-bottom: 12px; }}
  input[type=text], input[type=number], select, textarea {{ font: inherit; padding: 4px 8px; border: 1px solid #d0d7de; border-radius: 4px; }}
  button {{ font: inherit; padding: 6px 12px; border: 1px solid #d0d7de; border-radius: 4px; background: #f6f8fa; cursor: pointer; }}
  button:hover {{ background: #eaeef2; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
  th, td {{ border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; font-size: 13px; }}
  th {{ background: #f6f8fa; }}
  tr:nth-child(even) td {{ background: #fafbfc; }}
  .row-form td {{ background: #fff8c5; }}
  .status {{ margin-left: 8px; font-size: 13px; color: #57606a; }}
  .status.ok {{ color: #1a7f37; }}
  .status.err {{ color: #cf222e; }}
  details {{ margin-bottom: 12px; }}
  summary {{ cursor: pointer; font-size: 13px; color: #57606a; }}
</style>
</head>
<body>
<h1>Manual cost entry</h1>
<p>Active SPUs that have no manual cost row and no active procurement link.</p>

<details>
  <summary>API token (paste once; stored in localStorage)</summary>
  <div class="auth">
    <input type="text" id="token" placeholder="ttserp_rw_..." size="48">
    <button onclick="saveToken()">save</button>
    <span class="status" id="auth-status"></span>
  </div>
</details>

<table id="grid">
  <thead>
    <tr>
      <th style="width:120px">channel_product_id</th>
      <th style="width:200px">external_product_id</th>
      <th>title</th>
      <th style="width:80px">unit_cost</th>
      <th style="width:80px">currency</th>
      <th style="width:200px">note</th>
      <th style="width:80px"></th>
    </tr>
  </thead>
  <tbody id="rows">
    <tr><td colspan="7" id="placeholder">no token saved yet</td></tr>
  </tbody>
</table>

<script>
const TOKEN_KEY = "mc_token";
const $ = (id) => document.getElementById(id);

function saveToken() {{
  localStorage.setItem(TOKEN_KEY, $("token").value.trim());
  $("auth-status").textContent = "saved";
  setTimeout(() => $("auth-status").textContent = "", 1200);
  loadList();
}}

async function loadList() {{
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) {{ $("placeholder").textContent = "no token saved yet"; return; }}
  $("placeholder").textContent = "loading…";
  try {{
    const r = await fetch("/v2/reporting/missing-cost-products?limit=200", {{
      headers: {{ Authorization: "Bearer " + token }}
    }});
    if (!r.ok) throw new Error("HTTP " + r.status);
    const items = await r.json();
    const tbody = $("rows");
    tbody.innerHTML = "";
    if (!items.length) {{
      tbody.innerHTML = '<tr><td colspan="7">all SPUs have a cost or link — nothing to fill</td></tr>';
      return;
    }}
    for (const it of items) {{
      const tr = document.createElement("tr");
      tr.className = "row-form";
      tr.innerHTML = `
        <td>${{it.channel_product_id}}</td>
        <td>${{esc(it.external_product_id)}}</td>
        <td>${{esc(it.title || "")}}</td>
        <td><input type="number" step="0.0001" min="0.0001" data-k="unit_cost"></td>
        <td>
          <select data-k="currency">
            <option>USD</option><option>CNY</option><option>VND</option><option>EUR</option>
          </select>
        </td>
        <td><input type="text" data-k="note" maxlength="500" style="width:100%"></td>
        <td><button onclick="submit(this)">submit</button><span class="status"></span></td>
      `;
      tr.dataset.ext = it.external_product_id;
      tbody.appendChild(tr);
    }}
  }} catch (e) {{
    $("placeholder").textContent = "error: " + e.message;
  }}
}}

async function submit(btn) {{
  const tr = btn.closest("tr");
  const token = localStorage.getItem(TOKEN_KEY);
  const status = tr.querySelector(".status");
  const inputs = tr.querySelectorAll("input,select");
  const body = {{ channel_product_external_id: tr.dataset.ext }};
  for (const i of inputs) body[i.dataset.k] = i.value;
  status.textContent = "saving…";
  status.className = "status";
  try {{
    const r = await fetch("/v2/reporting/manual-costs", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json", Authorization: "Bearer " + token }},
      body: JSON.stringify(body),
    }});
    if (r.status === 201) {{
      status.textContent = "saved";
      status.className = "status ok";
      setTimeout(() => tr.remove(), 800);
    }} else {{
      const t = await r.text();
      status.textContent = "err " + r.status;
      status.className = "status err";
      status.title = t;
    }}
  }} catch (e) {{
    status.textContent = "err";
    status.className = "status err";
    status.title = String(e);
  }}
}}

function esc(s) {{
  return String(s).replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}})[c]);
}}

window.addEventListener("DOMContentLoaded", () => {{
  const saved = localStorage.getItem(TOKEN_KEY) || "";
  $("token").value = saved;
  if (saved) loadList();
}});
</script>
</body>
</html>
"""
