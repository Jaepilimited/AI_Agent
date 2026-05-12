"""Notion AI 사람만들기 로그 2nd 페이지에 누락된 업데이트 toggle 추가.

페이지: 3252b4283b00802aaff3f33f63ec91de
삽입 위치: "새로운 업데이트가 위에 추가됩니다." paragraph 바로 다음 (최신 toggle 위)
"""
import json
import sys
import urllib.request
from pathlib import Path

# Load token
TOKEN = None
for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
    line = line.strip()
    if line.startswith("NOTION_MCP_TOKEN="):
        TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")
        break

HDRS = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

PAGE_ID = "3252b428-3b00-802a-aff3-f33f63ec91de"
PARAGRAPH_AFTER_ID = "3252b428-3b00-81ea-ac19-fb98086bf7af"  # "새로운 업데이트가 위에 추가됩니다."


def t(s, bold=False, code=False):
    """rich_text helper."""
    return {"type": "text", "text": {"content": s}, "annotations": {"bold": bold, "code": code}}


def para(items):
    """paragraph block with rich_text items."""
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": items if isinstance(items, list) else [items]}}


def h2(s):
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [t(s)]}}


def h3(s):
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [t(s)]}}


def bullet(items, children=None):
    if isinstance(items, str):
        items = [t(items)]
    blk = {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": items}}
    if children:
        blk["bulleted_list_item"]["children"] = children
    return blk


def toggle(title_rich, children):
    return {"object": "block", "type": "toggle", "toggle": {"rich_text": title_rich, "children": children}}


def code_inline(s, txt_before="", txt_after=""):
    items = []
    if txt_before:
        items.append(t(txt_before))
    items.append(t(s, code=True))
    if txt_after:
        items.append(t(txt_after))
    return items


# ─────────────────────────── Toggle 1 (최신) ───────────────────────────
toggle1 = toggle(
    [t("📋 Update Log — 2026-05-11~12 (Drive 사진 검색 시스템 신규 + 정확도 100% 달성)")],
    [
        h2("변경 사항"),

        h3("1. [신규] face-search 시스템 — \"이게 뭐야?\" 사진 인식"),
        para(t("Drive 사진 라이브러리를 멀티모달 임베딩으로 인덱싱하고, 사용자가 사진을 업로드하면 모델/제품/매장 이벤트를 자동 식별.")),
        bullet(code_inline("SigLIP", txt_after=" (텍스트/이미지 멀티모달, 768-dim, 다국어) + ") + code_inline("InsightFace buffalo_l", txt_after=" (얼굴 검출+512-dim) + ") + code_inline("Tesseract 5.3", txt_after=" (영문/한글 OCR)")),
        bullet("인덱스 규모: 사진 6,463장 / 얼굴 1,403장 (Drive `01. Image` 폴더 전체 재귀 sync)"),
        bullet([t("신규 파일:")], [
            para(code_inline("app/agents/face_clip_agent.py", txt_after=" — 임베딩 + 검색 + 라벨 추출 + score-weighted vote")),
            para(code_inline("app/api/face_search_routes.py", txt_after=" — /face-search UI + /query + /thumb (thread-local Drive client)")),
            para(code_inline("scripts/face_clip_sync.py", txt_after=" — Drive → 로컬 인덱스 동기화 (NFC + Model anchor 라벨 규칙)")),
            para(code_inline("scripts/face_meta_repair.py", txt_after=" — 메타 in-place 보정 도구")),
            para(code_inline("scripts/eval_face_search.py", txt_after=" — 자동 ground truth + 병렬 thumb prefetch + JSON 리포트")),
            para(code_inline("scripts/eval_holdout.py", txt_after=" — 변형 사진(crop/brightness/회전)으로 일반화 검증")),
        ]),
        bullet(code_inline("app/main.py", txt_after=": face_search_router 등록 + 워밍업 task (OOM 위험으로 비활성화 주석)")),
        bullet("신규 endpoint: `/face-search` (HTML UI), `/face-search/query` (POST 이미지/텍스트), `/face-search/thumb/{drive_id}`, `/face-search/stats`"),

        h3("2. [정확도] 평가 파이프라인 + Phase 0~14 알고리즘 튜닝"),
        para(t("baseline 13.3% → Phase 14 100% (product label exact, 90장 평가). 14단계에 걸친 측정 + 픽스 + 재평가 루프.")),
        bullet([t("score-weighted vote + top1 score ≥ 0.95 강제 override", bold=True)]),
        bullet("라벨 추출 실패 항목 분모에서 제외 (confidence 보정 — 이전엔 자기 매칭에도 conf 40% 나오던 문제)"),
        bullet("`10. 썸네일용` 폴더 vote 제외 (라인 단독 라벨이 진짜 제품 라벨을 죽이던 문제)"),
        bullet("`face_results` 필터 — Model anchor 없는 face_meta 항목 vote 제외 (강아인 사진이 제품 라벨로 잘못 나오던 케이스 해결)"),
        bullet("OCR 통합 — face top1 < 0.5일 때만 호출 (얼굴 사진 skip). 패키지 텍스트로 라인 내 제품 disambiguation"),
        bullet("extract_label reversed-only + 잡음 segment 통일 (`기타`, `Etc.`, `OLD`, `리뉴얼 전 누끼`, 사이즈 숫자 regex)"),
        bullet("JBT를 product line으로 인정 (`_PRODUCT_LINES` 추가)"),

        h3("3. [품질] 메타 데이터 보정"),
        bullet("`face_meta.person_label` 240개 재추출 + 1,098개 정리 (Model anchor 규칙)"),
        bullet("NFC 정규화: clip 4,112 + face 2,065 + manifest 2,718 (macOS NFD 폴더명 → NFC, 한글 검색 매칭 실패 해소)"),
        bullet("`scripts/face_meta_repair.py`: 임베딩 재생성 없이 메타만 in-place 보정"),

        h2("테스트 결과"),
        bullet([t("90장 인덱스 평가 (person/product/event 각 30):", bold=True), t(" type "), t("100%", bold=True), t(" / label exact "), t("100%", bold=True), t(" / avg conf 0.98")]),
        bullet([t("60장 Holdout (변형 사진, 일반화):", bold=True), t(" type 98.3% / label exact 80% / conf 0.91")]),
        bullet("Calibration: conf 0.9-1.0 (50건) → 실제 94% 정확; conf 0.3-0.7 (8건) → 0% 정확 (시스템이 모를 땐 정직히 표시)"),

        h2("배포"),
        bullet([t("Dev (3001): 적용 완료. 첫 쿼리 ~30초 (lazy 모델 로드), 이후 1~2초", bold=True)]),
        bullet([t("Prod (3000): 반영 대기 — 주인님 허락 후 ", bold=True), t("pm2 reload skin1004-prod", code=True)]),

        h2("커밋"),
        bullet(code_inline("2dd786d", txt_after=" feat(face-search): Drive 사진 검색 시스템 + 정확도 개선 파이프라인")),
        bullet(code_inline("f5a05b5", txt_after=" feat(face-search): Phase 3~7 정확도 추가 개선")),
        bullet(code_inline("47e46fe", txt_after=" feat(face-search): Phase 8~14 — 100% 정확도 달성")),
        bullet(code_inline("b5d5694", txt_after=" test(face-search): holdout 검증 추가")),
        bullet(code_inline("c67cd0b", txt_after=" test(face-search): sanity check 결과 보존")),
    ],
)

# ─────────────────────────── Toggle 2 (덜 최신) ───────────────────────────
toggle2 = toggle(
    [t("Update Log — 2026-05-04~07 (GCP Ubuntu 마이그레이션 후속 정리 + 의존성 추가)")],
    [
        h2("변경 사항"),

        h3("1. [인프라] GCP Ubuntu 마이그레이션 후속 정리 및 안정성 개선"),
        para(t("Windows → GCP Ubuntu 인스턴스 전환 후속 작업. 시스템 안정성 확보 + 의존성 정리.")),
        bullet("작업 디렉토리 `/home/skin1004/AI_Agent`로 이전, PM2 ecosystem (`windowsHide: true` 유지)"),
        bullet("최신 변경 sync"),

        h3("2. [의존성] requirements_linux.txt 업데이트"),
        bullet(code_inline("playwright", txt_after=" + ") + code_inline("pyee", txt_after=" 추가 — E2E QA 자동화 의존성")),
        bullet(code_inline("ldap3", txt_after=" 추가 — AD 사용자 동기화 LDAP 클라이언트 (") + [t("scripts/sync_ad_users.py", code=True), t(" 의존성)")]),

        h3("3. [지식맵] knowledge_map 자동 업데이트"),
        bullet("매일 03:00 cron으로 `python scripts/build_knowledge_graph.py --force` 자동 실행"),
        bullet("121개 파일 / 519개 노드 / 997개 엣지 (commit 7c427ab 시점)"),

        h2("배포"),
        bullet("Dev (3001): 의존성 설치 + 재시작 완료"),
        bullet("Prod (3000): 반영 완료"),

        h2("커밋"),
        bullet(code_inline("58894dc", txt_after=" chore: sync latest changes for GCP Ubuntu migration")),
        bullet(code_inline("c7ca191", txt_after=" chore: GCP Ubuntu 마이그레이션 후속 정리 및 안정성 개선")),
        bullet(code_inline("c76c9a1", txt_after=" chore(deps): add playwright and pyee to requirements_linux.txt")),
        bullet(code_inline("7c427ab", txt_after=" chore(deps): add ldap3 to requirements_linux.txt + knowledge map auto-update")),
    ],
)


# ─────────────────────────── API call ───────────────────────────
def insert_toggles():
    body = {
        "children": [toggle1, toggle2],
        "after": PARAGRAPH_AFTER_ID,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
        data=data, headers=HDRS, method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        print("✅ inserted")
        for blk in resp.get("results", []):
            bt = blk.get("type")
            title = "".join(t.get("plain_text","") for t in blk.get(bt, {}).get("rich_text", []))
            print(f"  - [{bt}] id={blk['id']} | {title[:80]}")
    except urllib.error.HTTPError as e:
        print(f"❌ FAIL {e.code} {e.reason}")
        print(e.read().decode()[:1500])


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        # Validate JSON structure
        import json as _j
        out = _j.dumps({"children": [toggle1, toggle2]}, ensure_ascii=False, indent=2)
        print(f"payload size: {len(out)} chars")
        print(out[:1500])
    else:
        insert_toggles()
