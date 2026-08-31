"""공유드라이브 COA·MSDS 찾기 — 페이지와 조회 API.

⛔ 서비스계정을 쓰지 않는다. 사용자 본인 OAuth 로만 읽어 드라이브 권한을 그대로 따른다.
"""
from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.api.auth_middleware import get_current_user
from app.core import coa_finder as cf
from app.core.google_auth import GoogleAuthManager

logger = logging.getLogger(__name__)
router = APIRouter()

_auth = GoogleAuthManager()
_MAX_ROWS = 500
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_MAX_DOWNLOAD_ITEMS = 200
_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n]+')


def _credentials(email: str):
    return _auth.get_credentials(email)


@router.get("/coa-finder")
async def coa_finder_page(_: object = Depends(get_current_user)):
    return FileResponse("app/static/coa_finder.html", media_type="text/html")


@router.post("/api/coa-finder/search")
async def coa_finder_search(
    pasted: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    user: object = Depends(get_current_user),
):
    if file is not None:
        # ⛔ 읽어서 크기를 재는 것으로는 부족하다 — 다 받은 뒤에 재면 이미 큰 파일을
        #    통째로 받아 들고 있는 것이다. 받는 도중에 상한을 넘으면 그 자리에서 끊는다
        chunks: list[bytes] = []
        received = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > _MAX_UPLOAD_BYTES:
                # 여기서 멈췄으므로 전체 크기는 모른다 — 잰 만큼만 말한다
                raise HTTPException(
                    400,
                    f"파일이 {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB 상한을 넘습니다 "
                    f"(적어도 {received / (1024 * 1024):.1f}MB)")
            chunks.append(chunk)
        rows_source = ("xlsx", b"".join(chunks))
    elif pasted:
        pasted_bytes = len(pasted.encode("utf-8"))
        if pasted_bytes > _MAX_UPLOAD_BYTES:
            max_mb = _MAX_UPLOAD_BYTES // (1024 * 1024)
            over_mb = (pasted_bytes - _MAX_UPLOAD_BYTES) / (1024 * 1024)
            raise HTTPException(
                400,
                f"붙여넣은 텍스트가 {max_mb}MB 상한을 약 {over_mb:.1f}MB 초과합니다 "
                f"(현재 {pasted_bytes / (1024 * 1024):.1f}MB)")
        rows_source = ("pasted", pasted)
    else:
        raise HTTPException(400, "목록을 붙여넣거나 엑셀 파일을 올려주세요")

    try:
        rows = (cf.parse_xlsx(rows_source[1]) if rows_source[0] == "xlsx"
                else cf.parse_pasted(rows_source[1]))
    except cf.HeaderNotFound as exc:
        raise HTTPException(400, str(exc)) from exc

    if not rows:
        raise HTTPException(400, "행을 찾지 못했습니다 — 헤더 아래에 데이터가 있는지 확인하세요")
    if len(rows) > _MAX_ROWS:
        raise HTTPException(
            400, f"{len(rows)}행입니다. 한 번에 {_MAX_ROWS}행까지 처리합니다")

    creds = _credentials(user.email)
    if creds is None:
        # ⛔ 미연결을 '전부 없음' 으로 보여주면 조용한 오답이 된다
        raise HTTPException(409, "구글 계정이 연결되어 있지 않습니다. 먼저 연결해주세요")

    def _verdict(v: cf.Verdict) -> dict:
        return {
            "status": v.status,
            "note": v.note,
            "files": [{"id": f.id, "name": f.name, "link": f.web_link, "size": f.size}
                      for f in v.files],
        }

    def _stream():
        counts = {cf.FOUND: 0, cf.MANY: 0, cf.NONE: 0, cf.CHECK: 0}
        total = len(rows)
        completed = 0
        try:
            for i, res in enumerate(cf.find_all(creds, rows), start=1):
                counts[res.coa.status] = counts.get(res.coa.status, 0) + 1
                payload = {
                    "index": i, "total": total,
                    "sku": res.row.sku, "description": res.row.description,
                    "lot": res.row.lot,
                    "coa": _verdict(res.coa), "msds": _verdict(res.msds),
                }
                yield f"event: row\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                completed = i
        except Exception as exc:                      # noqa: BLE001
            # ⛔ 여기서 그냥 끊으면 12행에서 죽은 조회가 "12행 완료"로 보인다.
            #    끝났다는 신호(done)와 죽었다는 신호(error)는 다르게 보내야 한다
            logger.warning("coa_search_stream_failed",
                           extra={"completed": completed, "total": total,
                                  "error": str(exc)[:200]})
            error_payload = {
                "message": f"조회 중 오류가 발생했습니다: {str(exc)[:200]}",
                "completed": completed, "total": total,
            }
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
            return
        summary = {"total": total, "counts": counts}
        yield f"event: done\ndata: {json.dumps(summary, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # nginx 가 이 응답을 버퍼링하면 진행률을 보여준다는 이 엔드포인트의
            # 존재 이유가 프로덕션에서만 조용히 죽는다 (테스트에선 안 걸린다)
            "X-Accel-Buffering": "no",
        })


class DownloadItem(BaseModel):
    file_id: str
    sku: str = ""
    lot: str = ""
    name: str = ""


class DownloadRequest(BaseModel):
    items: list[DownloadItem]


def _fetch_file(creds, file_id: str) -> bytes:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req, chunksize=1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


_MAX_ZIP_NAME_BYTES = 180


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """글자 중간을 자르지 않는다 — ext4·macOS 는 이름을 바이트로 센다."""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _zip_name(item: DownloadItem) -> str:
    """어느 롯트 것인지 열어보지 않아도 알게 한다."""
    parts = [p for p in (item.sku, item.lot, item.name or item.file_id) if p]
    joined = _UNSAFE.sub("_", "_".join(parts))
    return _truncate_utf8(joined, _MAX_ZIP_NAME_BYTES)


@router.post("/api/coa-finder/download")
async def coa_finder_download(
    payload: DownloadRequest,
    user: object = Depends(get_current_user),
):
    items = payload.items
    if not items:
        raise HTTPException(400, "받을 파일을 선택해주세요")
    if len(items) > _MAX_DOWNLOAD_ITEMS:
        raise HTTPException(
            400,
            f"{len(items)}건입니다. 한 번에 {_MAX_DOWNLOAD_ITEMS}건까지 받을 수 있습니다 "
            f"({len(items) - _MAX_DOWNLOAD_ITEMS}건 초과)")
    for i, item in enumerate(items):
        if not item.file_id.strip():
            # ⛔ 빈 file_id 를 그냥 넘기면 ZIP 안에 이름 없는 항목이 생긴다
            raise HTTPException(400, f"{i + 1}번째 항목에 file_id 가 없습니다")

    creds = _credentials(user.email)
    if creds is None:
        raise HTTPException(409, "구글 계정이 연결되어 있지 않습니다. 먼저 연결해주세요")

    buf = io.BytesIO()
    failed: list[str] = []
    total = 0
    cap_hit = False
    max_mb = _MAX_DOWNLOAD_BYTES // (1024 * 1024)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for item in items:
            label = f"{item.sku} · {item.lot} · {item.name}"
            if cap_hit:
                # ⛔ 상한 초과 뒤에도 왜 못 받았는지 목록에 남긴다 — 조용히 빠지지 않게
                failed.append(f"{label}: 총 용량 상한({max_mb}MB) 초과로 받지 못함")
                continue
            try:
                data = _fetch_file(creds, item.file_id)
            except Exception as exc:                  # noqa: BLE001
                logger.warning("coa_download_failed",
                               extra={"file_id": item.file_id, "error": str(exc)[:200]})
                failed.append(f"{label}: {str(exc)[:120]}")
                continue

            if total + len(data) > _MAX_DOWNLOAD_BYTES:
                over_mb = (total + len(data) - _MAX_DOWNLOAD_BYTES) / (1024 * 1024)
                failed.append(
                    f"{label}: 총 용량 상한({max_mb}MB)을 약 {over_mb:.1f}MB 초과해 중단")
                cap_hit = True
                continue

            total += len(data)
            name = _zip_name(item)
            n, stem = 1, name
            while name in used:
                name, n = f"{stem}({n})", n + 1
            used.add(name)
            zf.writestr(name, data)

        if failed:
            # ⛔ 조용히 빠지면 아무도 모른다
            zf.writestr("_받지못한_목록.txt",
                        "받지 못한 파일\n\n" + "\n".join(failed))

    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="coa_msds.zip"'})
