"""FastAPI router — GET /upload-ui

Serves a self-contained browser upload page for a session.
The user opens the URL in a new tab, drags & drops files (or clicks to browse),
and the page POSTs each file to POST /upload on this same server.
Results (blob_urls) are displayed inline so the page never navigates away.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

upload_ui_router = APIRouter()

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Video Upload — {session_id}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f1117; color: #e2e8f0; min-height: 100vh;
    display: flex; flex-direction: column; align-items: center;
    padding: 2rem 1rem;
  }}
  h1 {{ font-size: 1.5rem; font-weight: 600; margin-bottom: .25rem; }}
  .session-id {{
    font-size: .75rem; color: #64748b; font-family: monospace;
    margin-bottom: 2rem;
  }}
  .drop-zone {{
    width: 100%; max-width: 640px;
    border: 2px dashed #334155; border-radius: 12px;
    padding: 3rem 2rem; text-align: center;
    cursor: pointer; transition: border-color .2s, background .2s;
    background: #1e293b;
  }}
  .drop-zone.drag-over {{ border-color: #6366f1; background: #1e1b4b; }}
  .drop-zone svg {{ width: 48px; height: 48px; color: #475569; margin-bottom: 1rem; }}
  .drop-zone p {{ color: #94a3b8; margin-bottom: .5rem; }}
  .drop-zone .hint {{ font-size: .8rem; color: #475569; }}
  .browse-btn {{
    display: inline-block; margin-top: 1rem; padding: .5rem 1.25rem;
    background: #6366f1; color: #fff; border-radius: 6px;
    font-size: .875rem; cursor: pointer; border: none;
    transition: background .2s;
  }}
  .browse-btn:hover {{ background: #4f46e5; }}
  #file-input {{ display: none; }}
  .queue {{ width: 100%; max-width: 640px; margin-top: 1.5rem; }}
  .file-row {{
    background: #1e293b; border-radius: 8px; padding: .75rem 1rem;
    margin-bottom: .5rem;
  }}
  .file-row .name {{ font-size: .875rem; font-weight: 500; margin-bottom: .4rem; }}
  .progress-wrap {{
    height: 4px; background: #334155; border-radius: 2px; overflow: hidden;
    margin-bottom: .4rem;
  }}
  .progress-bar {{
    height: 100%; background: #6366f1; width: 0%;
    transition: width .1s linear;
  }}
  .status {{ font-size: .75rem; color: #64748b; }}
  .status.done {{ color: #34d399; }}
  .status.error {{ color: #f87171; }}
  .blob-row {{
    display: flex; align-items: center; gap: .5rem; margin-top: .35rem;
    flex-wrap: wrap;
  }}
  .blob-url {{
    font-family: monospace; font-size: .7rem; color: #a5b4fc;
    word-break: break-all; flex: 1;
  }}
  .copy-btn {{
    flex-shrink: 0; padding: .2rem .6rem; font-size: .7rem;
    background: #334155; color: #e2e8f0; border: none;
    border-radius: 4px; cursor: pointer; transition: background .15s;
  }}
  .copy-btn:hover {{ background: #475569; }}
  .copy-btn.copied {{ background: #065f46; color: #6ee7b7; }}
</style>
</head>
<body>
<h1>Video Upload</h1>
<p class="session-id">Session: {session_id}{job_id_line}</p>

<div class="drop-zone" id="drop-zone">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
    <path stroke-linecap="round" stroke-linejoin="round"
      d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/>
  </svg>
  <p>Drag &amp; drop files here</p>
  <p class="hint">Any file type &bull; Multiple files supported</p>
  <button class="browse-btn" onclick="document.getElementById('file-input').click()">
    Browse files
  </button>
  <input type="file" id="file-input" multiple>
</div>

<div class="queue" id="queue"></div>

<script>
const SESSION_ID = {session_id_json};
const JOB_ID = new URLSearchParams(window.location.search).get("job") || "";
const UPLOAD_URL = "/upload";

const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const queue    = document.getElementById("queue");

dropZone.addEventListener("dragover",  e => {{ e.preventDefault(); dropZone.classList.add("drag-over"); }});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", e => {{
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  uploadFiles(e.dataTransfer.files);
}});
fileInput.addEventListener("change", () => {{
  uploadFiles(fileInput.files);
  fileInput.value = "";
}});

function uploadFiles(files) {{
  Array.from(files).forEach(uploadFile);
}}

function uploadFile(file) {{
  const row = document.createElement("div");
  row.className = "file-row";
  row.innerHTML = `
    <div class="name">${{escHtml(file.name)}}</div>
    <div class="progress-wrap"><div class="progress-bar" id="pb-${{uid()}}"></div></div>
    <div class="status" id="st-${{uid()}}">Uploading…</div>`;
  const pbId = row.querySelector(".progress-bar").id;
  const stId = row.querySelector(".status").id;
  queue.prepend(row);

  const fd = new FormData();
  fd.append("file", file, file.name);
  fd.append("filename", file.name);
  if (SESSION_ID) fd.append("session_id", SESSION_ID);
  if (JOB_ID) fd.append("job_id", JOB_ID);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", UPLOAD_URL);

  xhr.upload.onprogress = e => {{
    if (e.lengthComputable)
      document.getElementById(pbId).style.width = (e.loaded / e.total * 100) + "%";
  }};

  xhr.onload = () => {{
    document.getElementById(pbId).style.width = "100%";
    const st = document.getElementById(stId);
    if (xhr.status === 200) {{
      const data = JSON.parse(xhr.responseText);
      st.className = "status done";
      st.textContent = "Uploaded";
      const blobDiv = document.createElement("div");
      blobDiv.className = "blob-row";
      const copyId = "cp-" + uid();
      blobDiv.innerHTML = `
        <span class="blob-url">${{escHtml(data.blob_url)}}</span>
        <button class="copy-btn" id="${{copyId}}"
          onclick="copyUrl('${{copyId}}', '${{escAttr(data.blob_url)}}')">Copy</button>`;
      row.appendChild(blobDiv);
    }} else {{
      st.className = "status error";
      st.textContent = "Error: " + xhr.status + " — " + xhr.responseText;
    }}
  }};
  xhr.onerror = () => {{
    document.getElementById(stId).className = "status error";
    document.getElementById(stId).textContent = "Network error";
  }};
  xhr.send(fd);
}}

function copyUrl(btnId, url) {{
  navigator.clipboard.writeText(url).then(() => {{
    const btn = document.getElementById(btnId);
    btn.textContent = "Copied!";
    btn.classList.add("copied");
    setTimeout(() => {{ btn.textContent = "Copy"; btn.classList.remove("copied"); }}, 2000);
  }});
}}

let _uid = 0;
function uid() {{ return ++_uid; }}
function escHtml(s) {{
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}}
function escAttr(s) {{
  return s.replace(/'/g, "\\'");
}}
</script>
</body>
</html>
"""


@upload_ui_router.get("/upload-ui", response_class=HTMLResponse)
async def upload_ui(session: str = "", job: str = "") -> HTMLResponse:
    import json as _json
    job_id_line = f" &bull; Job: {job}" if job else ""
    html = _HTML.format(
        session_id=session or "no-session",
        session_id_json=_json.dumps(session or None),
        job_id_line=job_id_line,
    )
    return HTMLResponse(content=html)
