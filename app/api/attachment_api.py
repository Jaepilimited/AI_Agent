# -*- coding: utf-8 -*-
"""올린 엑셀·CSV 를 표 텍스트로 돌려준다 (붐따 #161).

⛔ 파일을 **저장하지 않는다.** 읽어서 텍스트로 바꿔 돌려줄 뿐이고, 그 텍스트는
   사용자의 메시지에 실려 대화에 남는다. 서버에 남는 사본이 없으므로 보관·삭제·
   권한을 새로 설계할 것이 없다 (가장 안전한 저장은 저장하지 않는 것이다).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.auth_middleware import get_current_user
from app.core import table_attachment as ta

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/attachments/table")
async def read_table(
    file: UploadFile = File(...),
    user: object = Depends(get_current_user),
):
    filename = file.filename or "attachment"
    # ⛔ 다 받은 뒤에 크기를 재지 마라 — 그때는 이미 통째로 들고 있는 것이다
    #    (COA 찾기 업로드와 같은 방식)
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        received += len(chunk)
        if received > ta.MAX_BYTES:
            raise HTTPException(
                400,
                f"파일이 {ta.MAX_BYTES // (1024 * 1024)}MB 상한을 넘습니다 "
                f"(적어도 {received / (1024 * 1024):.1f}MB). 필요한 시트·행만 "
                "남겨 다시 올려 주세요.")
        chunks.append(chunk)

    try:
        table = ta.parse(filename, b"".join(chunks))
    except ta.UnsupportedFile as e:
        # 메시지는 그대로 사용자에게 간다 — 무엇을 하면 되는지 적혀 있다
        raise HTTPException(400, str(e))
    except Exception as e:                    # noqa: BLE001
        logger.warning("table_attachment_failed",
                       extra={"file": filename[:80], "error": str(e)[:200]})
        raise HTTPException(
            400,
            "파일을 읽지 못했습니다. 표를 복사해 채팅창에 붙여넣어 주세요.")

    logger.info("table_attachment_read",
                extra={"file": filename[:80], "rows": table.total_rows,
                       "cols": table.total_cols})
    return {
        "filename": filename,
        "sheet": table.sheet,
        "other_sheets": table.other_sheets,
        "rows": table.total_rows,
        "cols": table.total_cols,
        "included_rows": len(table.rows),
        "truncated_rows": table.truncated_rows,
        "truncated_cols": table.truncated_cols,
        # ⚠️ 탭 구분이어야 한다 — `_has_pasted_data` 가 탭을 보고 "사용자가
        #    가져온 표" 로 판정하고, 그 판정이 조회로 덮어쓰는 것을 막는다
        "tsv": table.to_tsv(),
        "note": table.note(filename),
        # ⛔ 본문에 붙일 덩어리는 **서버가 만든다** — 표식이 프론트에 사본으로
        #    있으면 갈리고, 갈리면 붙여넣은 표 판정이 조용히 풀린다
        "block": table.to_block(filename),
    }
