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
        parsed = (cf.parse_xlsx(rows_source[1]) if rows_source[0] == "xlsx"
                  else cf.parse_pasted(rows_source[1]))
    except cf.HeaderNotFound as exc:
        raise HTTPException(400, str(exc)) from exc
    rows = parsed.rows

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
        counts = {cf.FOUND: 0, cf.MANY: 0, cf.NONE: 0, cf.CHECK: 0, cf.FAILED: 0}
        # ⛔ MSDS 쪽 실패는 counts 가 세지 않는다 (COA 상태만 센다) — 따로 센다
        msds_failed = 0
        total = len(rows)
        completed = 0
        try:
            for i, res in enumerate(cf.find_all(creds, rows), start=1):
                counts[res.coa.status] = counts.get(res.coa.status, 0) + 1
                if res.msds.status == cf.FAILED:
                    msds_failed += 1
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
        summary = {
            "total": total, "counts": counts,
            "msds_failed": msds_failed,
            # 말없이 버린 행 — 화면이 건수를 밝힌다
            "skipped_no_sku": parsed.skipped_no_sku,
            # ⛔ 화면이 상한의 사본을 들고 있으면 조용히 어긋난다. 서버가 알려주고,
            #    못 받으면 화면은 나누지 않는다 (그때도 서버가 사실대로 자른다)
            "max_download_items": _MAX_DOWNLOAD_ITEMS,
            "max_download_bytes": _MAX_DOWNLOAD_BYTES,
        }
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
    # 화면의 판정. ⛔ 없거나 모르는 값이면 **확정으로 보지 않는다** — 이 엔드포인트는
    #    임의 JSON 을 받으므로 클라이언트가 빠뜨린 것을 '찾음' 으로 읽으면
    #    확인되지 않은 문서가 확정된 이름으로 고객에게 나간다
    status: str = ""
    # "coa" | "msds". MSDS 는 제품 단위라 롯트가 없다. 모르면 롯트를 지니는
    # 쪽(coa)으로 본다 — 그쪽은 status 규칙이 계속 지킨다
    kind: str = ""


class DownloadRequest(BaseModel):
    items: list[DownloadItem]


class _DownloadBudgetExceeded(Exception):
    """예산을 넘겨 중단했다. `received` 는 **잰 만큼**이지 파일 크기가 아니다."""

    def __init__(self, received: int) -> None:
        self.received = received
        super().__init__(f"용량 예산 초과 (적어도 {received} 바이트)")


def _fetch_file(creds, file_id: str, budget: int) -> bytes:
    """⛔ 다 받은 뒤에 크기를 재지 마라 — 그때는 이미 통째로 메모리에 들고 있다.

    `file_id` 는 매칭 결과가 아니라 **클라이언트가 주는 임의 값**이다.
    사용자가 읽을 수 있는 큰 파일 id 하나면 WAS 메모리가 그만큼 올라간다.
    1MB 청크 사이에서 예산을 넘으면 그 자리에서 끊고 받은 것을 버린다
    (업로드 상한을 받는 도중에 끊는 것과 같은 방식).
    """
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req, chunksize=1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
        if buf.tell() > budget:
            received = buf.tell()
            buf.close()                   # 부분 수신분을 들고 있지 않는다
            raise _DownloadBudgetExceeded(received)
    return buf.getvalue()


_MAX_ZIP_NAME_BYTES = 180


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """글자 중간을 자르지 않는다 — ext4·macOS 는 이름을 바이트로 센다."""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


_MSDS_KIND = "msds"
_UNCONFIRMED_PREFIX = "확인필요_"
_UNCONFIRMED_LIST = "_확인필요_목록.txt"

# 상태별로 '왜 확정이 아닌가'. 모르는 값은 맨 아래 기본 사유로 떨어진다
_UNCONFIRMED_REASON = {
    cf.MANY: "여러 후보 중 하나입니다 — 어느 것이 맞는지 확인되지 않았습니다",
    cf.CHECK: "확인필요 판정입니다 — 롯트가 정확히 맞는지 열어서 확인하세요",
    cf.NONE: "없음 판정인데 파일이 딸려 왔습니다 — 확인되지 않은 문서입니다",
    # ⛔ 이 항목이 없으면 조회실패가 "상태가 전달되지 않았습니다" 로 적힌다 —
    #    전달은 됐고 그 값이 '조회실패' 였으니 그건 사실이 아니다
    cf.FAILED: ("드라이브 조회가 실패해 이 행은 판정되지 않았습니다 — "
                "이 파일이 그 롯트 것인지 확인되지 않았습니다"),
}
_UNKNOWN_STATUS_REASON = ("판정 상태가 전달되지 않았습니다 — "
                          "확인되지 않은 문서로 취급합니다")


def _is_msds(item: DownloadItem) -> bool:
    """모르면 COA 로 본다 — 롯트를 지니는 쪽이고, 확정 판정 규칙이 그쪽을 지킨다."""
    return (item.kind or "").strip().casefold() == _MSDS_KIND


def _is_confirmed(item: DownloadItem) -> bool:
    """⛔ 없거나 모르는 상태는 확정이 아니다. 빠뜨린 클라이언트를 믿지 않는다."""
    return (item.status or "").strip() == cf.FOUND


def _label(item: DownloadItem) -> str:
    """목록에 적는 사람이 읽는 이름. MSDS 에는 롯트를 붙이지 않는다."""
    middle = "MSDS (롯트 무관)" if _is_msds(item) else (item.lot or "롯트 없음")
    return f"{item.sku} · {middle} · {item.name}"


def _zip_name(item: DownloadItem) -> str:
    """어느 롯트 것인지 열어보지 않아도 알게 한다.

    ⛔ 화면의 판정을 여기서 지우지 마라. ZIP 은 그대로 고객에게 전달되는
       산출물이라, 확인필요·여러건이 확정된 이름으로 나가면 화면에만 있던
       경고는 그 시점에 사라진다.
    ⛔ MSDS 에는 롯트를 붙이지 않는다 — 제품 단위 문서라 롯트가 없는데
       이름이 롯트를 주장하면 롯트가 맞는 문서인 것처럼 보인다.
    """
    if _is_msds(item):
        parts = [item.sku, "MSDS", item.name or item.file_id]
    else:
        parts = [item.sku, item.lot, item.name or item.file_id]
    joined = _UNSAFE.sub("_", "_".join(p for p in parts if p))
    if not _is_confirmed(item):
        joined = _UNCONFIRMED_PREFIX + joined
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
    failed: list[str] = []          # 조회·다운로드 자체가 안 된 것
    cap_cause = ""                  # 남은 예산을 바닥낸 그 파일 (원인)
    displaced: list[str] = []       # 그것 때문에 밀린 파일들 (결과)
    unconfirmed: list[str] = []
    written = 0
    total = 0
    cap_hit = False
    max_mb = _MAX_DOWNLOAD_BYTES // (1024 * 1024)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for item in items:
            label = _label(item)
            if cap_hit:
                # ⛔ 상한 초과 뒤에도 왜 못 받았는지 목록에 남긴다 — 조용히 빠지지 않게.
                #    ⚠️ 원인이 된 파일과 그 때문에 밀린 파일은 **다른 사실**이라
                #    문구가 확실히 갈라져야 한다 (예전엔 둘이 거의 같아 보였다)
                displaced.append(f"{label}: 앞 파일에서 용량이 차서 시도하지 않았습니다")
                continue
            try:
                data = _fetch_file(creds, item.file_id, _MAX_DOWNLOAD_BYTES - total)
            except _DownloadBudgetExceeded as exc:
                # ⛔ 재지 않은 총 크기를 주장하지 않는다 — 받다 끊었으므로
                #    이 파일이 얼마나 큰지는 모른다 (업로드 상한과 같은 규칙)
                logger.warning("coa_download_budget_exceeded",
                               extra={"file_id": item.file_id,
                                      "received": exc.received})
                cap_cause = (
                    f"{label}: 여기서 남은 용량 예산이 바닥났습니다 "
                    f"— 총 상한 {max_mb}MB 중 적어도 "
                    f"{exc.received / (1024 * 1024):.1f}MB 를 받다 중단했습니다")
                cap_hit = True
                continue
            except Exception as exc:                  # noqa: BLE001
                logger.warning("coa_download_failed",
                               extra={"file_id": item.file_id, "error": str(exc)[:200]})
                failed.append(f"{label}: {str(exc)[:120]}")
                continue

            total += len(data)
            name = _zip_name(item)
            n, stem = 1, name
            while name in used:
                name, n = f"{stem}({n})", n + 1
            used.add(name)
            zf.writestr(name, data)
            written += 1
            if not _is_confirmed(item):
                reason = _UNCONFIRMED_REASON.get(
                    (item.status or "").strip(), _UNKNOWN_STATUS_REASON)
                unconfirmed.append(f"{label}\n    → {name}\n    {reason}")

        if failed or cap_cause or displaced:
            # ⛔ 조용히 빠지면 아무도 모른다. 원인(예산을 바닥낸 파일)을 맨 위에 두고
            #    그것 때문에 밀린 파일들을 그 아래에 둔다 — 읽는 사람이 원인과
            #    피해자를 구분할 수 있어야 한다
            body = ["받지 못한 파일", ""]
            if failed:
                body += ["[조회·다운로드 실패]"] + failed + [""]
            if cap_cause:
                body += ["[용량 상한으로 중단]", cap_cause]
                if displaced:
                    body += ["", "그래서 아래 파일들은 시도조차 하지 않았습니다:"]
                    body += ["  - " + line for line in displaced]
                body += [""]
            elif displaced:                      # 이론상 없는 경우지만 삼키지 않는다
                body += ["[용량 상한으로 중단]"] + displaced + [""]
            zf.writestr("_받지못한_목록.txt", "\n".join(body))
        if unconfirmed:
            # ⛔ 실패(_받지못한_목록)와 불확실은 다른 것이라 목록도 따로 둔다.
            #    섞으면 "못 받았다" 와 "받았는데 맞는지 모른다" 가 한 덩어리가 된다
            zf.writestr(
                _UNCONFIRMED_LIST,
                "확인이 필요한 파일 (이 롯트 것이라고 확정되지 않았습니다)\n"
                "⛔ 고객에게 전달하기 전에 사람이 열어서 확인하세요.\n\n"
                + "\n\n".join(unconfirmed) + "\n")

    buf.seek(0)
    skipped = len(items) - written
    if skipped:
        logger.warning("coa_download_partial",
                       extra={"requested": len(items), "written": written,
                              "skipped": skipped})
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="coa_msds.zip"',
            # ⛔ 반쪽 ZIP 이 평범한 ZIP 과 똑같이 내려온다 — 열어보기 전에는 모른다.
            #    ZIP **안에만** 진실이 있으면 화면은 성공한 것처럼 보이므로
            #    밖에도 적는다. 값은 ASCII 숫자만 (헤더에 한글을 넣으면 깨진다)
            "X-COA-Items-Requested": str(len(items)),
            "X-COA-Items-Written": str(written),
            "X-COA-Items-Skipped": str(skipped),
        })
