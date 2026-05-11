"""Drive 사진 인덱스 검색 — 텍스트/이미지 쿼리.

Endpoints:
  GET  /face-search                 → HTML UI
  POST /face-search/query           → text or image query → JSON results
  GET  /face-search/thumb/{drive_id}→ proxy Drive thumbnail (auth via service account)
  GET  /face-search/stats           → index counts
"""
from __future__ import annotations

import asyncio
import io
import threading
import time
from typing import Optional

import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

logger = structlog.get_logger(__name__)
router = APIRouter()

KEY_PATH = "/home/skin1004/keys/skin1004-319714-60527c477460.json"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Drive API client는 thread-safe하지 않음 — thread별 인스턴스 분리.
# (singleton + 동시 호출 시 SSL "WRONG_VERSION_NUMBER" 에러 발생)
_drive_local = threading.local()


def _get_drive():
    drive = getattr(_drive_local, "drive", None)
    if drive is None:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        _drive_local.drive = drive
    return drive


@router.get("/face-search", response_class=HTMLResponse)
async def face_search_page():
    return HTMLResponse(_HTML)


@router.get("/face-search/stats")
async def face_search_stats():
    from app.agents import face_clip_agent
    return face_clip_agent.index_stats()


@router.post("/face-search/query")
async def face_search_query(
    text: Optional[str] = Form(None),
    top_k: int = Form(12),
    folder: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
):
    from app.agents import face_clip_agent

    if not text and not image:
        raise HTTPException(400, "either 'text' or 'image' is required")

    t0 = time.perf_counter()
    img_bytes: Optional[bytes] = None
    if image is not None:
        img_bytes = await image.read()

    # Run in thread to avoid blocking event loop (CLIP/InsightFace are CPU-bound)
    def _do_query():
        clip_results: list[dict] = []
        face_results: list[dict] = []

        if img_bytes is not None:
            qvec = face_clip_agent.embed_image_bytes(img_bytes)
            clip_results = face_clip_agent.search_clip(qvec, top_k=top_k, folder_filter=folder)
            try:
                fvec = face_clip_agent.embed_face_bytes(img_bytes)
                if fvec is not None:
                    face_results = face_clip_agent.search_face(fvec, top_k=top_k)
            except Exception as e:
                logger.warning("face_embed_failed", error=str(e)[:120])
        else:
            qvec = face_clip_agent.embed_text(text or "")
            clip_results = face_clip_agent.search_clip(qvec, top_k=top_k, folder_filter=folder)

        return clip_results, face_results

    clip_results, face_results = await asyncio.to_thread(_do_query)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Derive answer — face와 clip 둘 다 계산 후 신뢰도 더 높은 쪽 선택.
    # 단, face 매칭이 강하면(top1 score >= 0.5) 인물 식별로는 face가 항상 더 정확하므로 우선.
    face_ans = face_clip_agent.derive_answer(face_results, consider_top=5) if face_results else None
    clip_ans = face_clip_agent.derive_answer(clip_results, consider_top=5)
    if face_ans:
        face_ans["source"] = "face"
    if clip_ans:
        clip_ans["source"] = "clip"

    face_top1 = (face_results[0].get("score", 0.0) if face_results else 0.0)
    if face_ans and face_top1 >= 0.5:
        answer = face_ans
    elif face_ans and clip_ans:
        answer = face_ans if face_ans.get("confidence", 0) > clip_ans.get("confidence", 0) else clip_ans
    else:
        answer = face_ans or clip_ans

    return JSONResponse({
        "query_type": "image" if img_bytes else "text",
        "query": text if text else f"<image:{image.filename}>" if image else "",
        "elapsed_ms": round(elapsed_ms, 1),
        "answer": answer,
        "clip_results": clip_results,
        "face_results": face_results,
    })


@router.get("/face-search/thumb/{drive_id}")
async def face_search_thumb(drive_id: str, size: int = 400):
    """Proxy Drive thumbnail (small image) via service account auth."""
    from googleapiclient.http import MediaIoBaseDownload

    def _fetch():
        drive = _get_drive()
        # Try thumbnailLink first (faster, no full download)
        try:
            meta = drive.files().get(
                fileId=drive_id,
                fields="thumbnailLink,name,mimeType",
                supportsAllDrives=True,
            ).execute()
        except Exception as e:
            raise HTTPException(404, f"Drive file not found: {e}")

        # Fallback: download original (capped). For now just download.
        req = drive.files().get_media(fileId=drive_id, supportsAllDrives=True)
        buf = io.BytesIO()
        d = MediaIoBaseDownload(buf, req, chunksize=512 * 1024)
        done = False
        while not done:
            _, done = d.next_chunk()
        raw = buf.getvalue()

        # Resize via PIL to thumbnail size
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > size:
            ratio = size / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=82)
        return out.getvalue(), meta.get("mimeType", "image/jpeg")

    try:
        data, _ = await asyncio.to_thread(_fetch)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("thumb_fetch_failed", drive_id=drive_id, error=str(e)[:200])
        raise HTTPException(500, f"thumb error: {e}")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=3600"})


_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>이게 뭐야? — 사진 인식</title>
<style>
  *{box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans KR",sans-serif;margin:0;padding:24px;background:#fafafa;color:#1a1a1a;max-width:1100px;margin-left:auto;margin-right:auto}
  .header{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:16px}
  .header h1{margin:0;font-size:24px}
  .stats{color:#666;font-size:13px}
  .drop-zone{border:3px dashed #c4d2f0;background:#fff;border-radius:16px;padding:48px 24px;text-align:center;cursor:pointer;transition:all .15s;margin-bottom:16px}
  .drop-zone.drag-over{border-color:#1a73e8;background:#e8f0fe;transform:scale(1.01)}
  .drop-zone:hover{border-color:#8ab2f0}
  .drop-icon{font-size:56px;margin-bottom:12px;color:#8ab2f0}
  .drop-text{font-size:18px;color:#1a73e8;font-weight:600;margin-bottom:6px}
  .drop-hint{font-size:13px;color:#666}
  .drop-zone input[type=file]{display:none}
  .text-search{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:10px;margin-bottom:16px;display:flex;gap:8px;align-items:center}
  .text-search input{flex:1;border:0;outline:0;padding:8px;font-size:14px}
  .text-search button{padding:8px 16px;background:#5f6368;color:#fff;border:0;border-radius:6px;cursor:pointer;font-size:13px}
  .text-search button:hover{background:#3c4043}
  .answer-card{background:#fff;border-radius:16px;padding:24px;margin:16px 0;display:grid;grid-template-columns:200px 1fr;gap:24px;align-items:center;box-shadow:0 2px 8px rgba(0,0,0,.06);border:2px solid #1a73e8}
  .answer-card.low{border-color:#fbbc04;background:linear-gradient(135deg,#fef7e0,#fff)}
  .answer-card.face{border-color:#34a853}
  .uploaded-preview{width:200px;height:200px;border-radius:12px;overflow:hidden;background:#f0f0f0;display:flex;align-items:center;justify-content:center;color:#aaa;font-size:12px}
  .uploaded-preview img{width:100%;height:100%;object-fit:cover}
  .answer-body .sentence{font-size:18px;line-height:1.5;color:#1a1a1a;margin-bottom:10px}
  .answer-body .sentence b{color:#1a73e8;font-size:22px}
  .answer-card.low .answer-body .sentence b{color:#b06000}
  .answer-card.face .answer-body .sentence b{color:#1e8e3e}
  .answer-meta{font-size:13px;color:#5f6368;margin-top:8px}
  .badges{margin-top:8px}
  .badge{display:inline-block;background:#e8eaed;color:#3c4043;padding:3px 10px;border-radius:12px;font-size:12px;margin-right:6px}
  .badge-line{background:#1a73e8;color:#fff}
  .toggle-evidence{display:inline-block;color:#1a73e8;font-size:13px;cursor:pointer;margin-top:10px;text-decoration:underline}
  .evidence{display:none;margin-top:16px}
  .evidence.shown{display:block}
  .section-title{font-size:14px;font-weight:600;margin-top:20px;margin-bottom:8px;color:#444}
  .results{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
  .card{background:#fff;border:1px solid #e5e5e5;border-radius:8px;overflow:hidden;cursor:pointer;transition:transform .1s}
  .card:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.08)}
  .thumb{width:100%;aspect-ratio:1;background:#f0f0f0;display:flex;align-items:center;justify-content:center;color:#aaa;font-size:12px}
  .thumb img{width:100%;height:100%;object-fit:cover}
  .meta{padding:8px}
  .score{font-weight:600;color:#1a73e8}
  .path{font-size:11px;color:#666;line-height:1.3;word-break:break-all;margin-top:4px}
  .face-tag{display:inline-block;background:#34a853;color:#fff;font-size:10px;padding:1px 6px;border-radius:3px;margin-left:4px}
  .empty{color:#999;font-style:italic;padding:20px;text-align:center;background:#fff;border-radius:8px}
  .loading{text-align:center;padding:40px;color:#1a73e8;font-size:16px}
  .loading-spinner{display:inline-block;width:24px;height:24px;border:3px solid #c4d2f0;border-top-color:#1a73e8;border-radius:50%;animation:spin 1s linear infinite;vertical-align:middle;margin-right:10px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .err{color:#d33;background:#fce8e6;padding:16px;border-radius:8px}
  .timing{color:#888;font-size:12px;margin-bottom:8px}
  @media (max-width: 700px) {
    .answer-card{grid-template-columns:1fr}
    .uploaded-preview{width:100%;height:auto;aspect-ratio:1;max-width:280px;margin:0 auto}
  }
</style>
</head>
<body>

<div class="header">
  <h1>🔍 이게 뭐야? — 사진 인식</h1>
  <div class="stats" id="stats">로딩 중...</div>
</div>

<div class="drop-zone" id="dropZone" onclick="document.getElementById('imageFile').click()">
  <input type="file" id="imageFile" accept="image/*" onchange="onFileChosen()">
  <div class="drop-icon">📷</div>
  <div class="drop-text">사진을 끌어다 놓거나 클릭해서 업로드</div>
  <div class="drop-hint">JPG, PNG · 모델 사진이면 인물 / 제품 사진이면 화장품 이름을 알려드립니다</div>
</div>

<details>
  <summary style="cursor:pointer;color:#5f6368;font-size:13px;margin-bottom:8px">텍스트로 검색하기 (선택)</summary>
  <div class="text-search">
    <input type="text" id="textQuery" placeholder="예: 센텔라 앰플, 여자 모델, retinol bottle" onkeydown="if(event.key==='Enter')runQuery('text')">
    <button onclick="runQuery('text')">검색</button>
  </div>
</details>

<div id="results"></div>

<script>
let lastUploadedDataUrl = null;

async function loadStats() {
  try {
    const r = await fetch('/face-search/stats');
    const j = await r.json();
    document.getElementById('stats').textContent =
      `인덱스: ${j.clip_count.toLocaleString()}장 사진 · ${j.face_count.toLocaleString()}장 얼굴`;
  } catch (e) {
    document.getElementById('stats').textContent = '(stats error)';
  }
}
loadStats();

// Drag and drop
const dz = document.getElementById('dropZone');
['dragover','dragenter'].forEach(ev => dz.addEventListener(ev, e => {
  e.preventDefault(); dz.classList.add('drag-over');
}));
['dragleave','drop'].forEach(ev => dz.addEventListener(ev, e => {
  e.preventDefault(); dz.classList.remove('drag-over');
}));
dz.addEventListener('drop', e => {
  if (e.dataTransfer.files.length > 0) {
    document.getElementById('imageFile').files = e.dataTransfer.files;
    onFileChosen();
  }
});

function onFileChosen() {
  const f = document.getElementById('imageFile').files[0];
  if (!f) return;
  // Read for preview
  const reader = new FileReader();
  reader.onload = e => { lastUploadedDataUrl = e.target.result; };
  reader.readAsDataURL(f);
  // Auto-trigger search
  runQuery('image');
}

async function runQuery(mode) {
  const fd = new FormData();
  fd.append('top_k', 12);

  if (mode === 'text') {
    const t = document.getElementById('textQuery').value.trim();
    if (!t) { alert('텍스트 입력해주세요'); return; }
    fd.append('text', t);
    lastUploadedDataUrl = null;
  } else {
    const f = document.getElementById('imageFile').files[0];
    if (!f) return;
    fd.append('image', f);
  }

  document.getElementById('results').innerHTML = '<div class="loading"><span class="loading-spinner"></span>분석 중... (첫 쿼리는 모델 로드로 30~60초)</div>';

  try {
    const r = await fetch('/face-search/query', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    const j = await r.json();
    renderResults(j);
  } catch (e) {
    document.getElementById('results').innerHTML = `<div class="err">에러: ${escapeHtml(String(e))}</div>`;
  }
}

function renderResults(j) {
  let html = '';

  if (j.answer) {
    html += renderAnswerCard(j.answer, j.query_type);
  } else {
    html += '<div class="empty">매칭 결과를 찾을 수 없습니다.</div>';
  }

  html += `<div class="timing">${j.elapsed_ms.toFixed(0)}ms · CLIP ${j.clip_results.length}건${j.face_results.length>0 ? ', 얼굴 매칭 '+j.face_results.length+'건' : ''}</div>`;

  html += `<a class="toggle-evidence" onclick="toggleEvidence()">근거 사진 보기/숨기기 ▾</a>`;
  html += '<div class="evidence" id="evidence">';
  html += `<div class="section-title">📷 비슷한 사진 (Top ${j.clip_results.length})</div>`;
  html += renderGrid(j.clip_results);
  if (j.face_results && j.face_results.length > 0) {
    html += `<div class="section-title">👤 얼굴 매칭 (Top ${j.face_results.length})</div>`;
    html += renderGrid(j.face_results, true);
  }
  html += '</div>';

  document.getElementById('results').innerHTML = html;
}

function renderAnswerCard(a, qType) {
  const isLow = a.confidence < 0.5;
  const isFace = a.source === 'face';
  const cls = `answer-card${isLow ? ' low' : ''}${isFace ? ' face' : ''}`;

  // Convert **text** to <b>text</b> for the sentence
  const sentenceHtml = escapeHtml(a.sentence).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');

  let preview = '';
  if (qType === 'image' && lastUploadedDataUrl) {
    preview = `<div class="uploaded-preview"><img src="${lastUploadedDataUrl}" alt="업로드"></div>`;
  } else {
    const icon = a.type === 'person' ? '👤' : a.type === 'product' ? '🧴' : a.type === 'event' ? '🏪' : '📁';
    preview = `<div class="uploaded-preview" style="font-size:64px">${icon}</div>`;
  }

  let badges = '';
  if (a.related_lines && a.related_lines.length > 0) {
    badges = '<div class="badges">' + a.related_lines.map(l => `<span class="badge badge-line">${escapeHtml(l)}</span>`).join('') + '</div>';
  }

  return `<div class="${cls}">
    ${preview}
    <div class="answer-body">
      <div class="sentence">${sentenceHtml}</div>
      <div class="answer-meta">
        Top ${a.considered}장 중 ${a.match_count}장 일치 · 평균 유사도 ${a.avg_score.toFixed(3)} · ${isFace ? '얼굴 인식' : '이미지 유사도'}
      </div>
      ${badges}
    </div>
  </div>`;
}

function toggleEvidence() {
  document.getElementById('evidence').classList.toggle('shown');
}

function renderGrid(items, isFace) {
  if (!items || items.length === 0) return '<div class="empty">결과 없음</div>';
  return '<div class="results">' + items.map(m => `
    <div class="card" onclick="window.open('https://drive.google.com/file/d/${m.drive_id}/view', '_blank')">
      <div class="thumb">
        <img loading="lazy" src="/face-search/thumb/${m.drive_id}" alt="${escapeHtml(m.name)}" onerror="this.parentNode.textContent='로드 실패'">
      </div>
      <div class="meta">
        <div><span class="score">${m.score.toFixed(3)}</span>${m.has_face || isFace ? '<span class="face-tag">얼굴</span>' : ''}</div>
        <div class="path" title="${escapeHtml(m.path)}">${escapeHtml(m.path)}</div>
      </div>
    </div>
  `).join('') + '</div>';
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
</script>

</body>
</html>
"""
