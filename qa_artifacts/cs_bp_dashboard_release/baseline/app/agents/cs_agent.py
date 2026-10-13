"""CS DB Agent — Customer Service Q&A from Google Spreadsheet.

Reads ~1,100 Q&A rows from 13 tabs (SKIN1004, COMMONLABS, ZOMBIE BEAUTY),
caches in memory, and answers CS-related questions via keyword matching
+ word overlap scoring + LLM synthesis.

Spreadsheet structure:
  - 제품문의_리스트(PM->BM): 문의일자, 팀, 브랜드, 라인, 질문, 답변
  - 비건인증: 브랜드, 제품명, 비건/PETA 인증 상태
  - 공통(SKIN1004): 카테고리, 질문, 답변
  - 제품 사용 루틴: 루틴 설명 텍스트
  - Product-specific tabs: 제품명, 질문, 답변 (or just 질문, 답변)
  - 제품CS(COMMONLABS): 제품명, 질문, 답변
  - 제품CS(ZOMBIE BEAUTY): 질문, 답변
"""

import asyncio
import re
import time as _time
from typing import Dict, List, Optional

import structlog
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from app.config import get_settings
from app.core import product_lines
from app.core.llm import get_flash_client, get_llm_client

logger = structlog.get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# ── Module-level cache ──
# ⛔ **이 캐시는 오래 조용히 낡아 있었다** (2026-09-03 사용자 제보:
#    *"CS DB와 제품정보에 대한 DB가 최신정보가 아닌거같은데"*).
#    `warmup()` 이 **서버 기동 시 한 번**만 불렸고 스케줄 잡도 TTL 도 없었다 —
#    CS 시트를 고쳐도 **재기동 전까지 반영되지 않는다.** 실측(프로덕션):
#    시트 최종수정 09-03 09:41 / 그 직전 재기동은 **09-02 16:18** 이었다.
#    ⚠️ 에러가 아니라 **옛 답을 자신 있게** 내놓는 형태라 아무도 못 알아챈다.
#    지금은 `cs_cache_hourly` 잡이 매시 :40 에 `refresh()` 를 부른다.
_qa_cache: List[Dict[str, str]] = []
_cache_loaded: bool = False
_loaded_at: Optional[float] = None

# ── Tab → brand mapping ──
_TAB_BRAND_MAP = {
    "제품문의_리스트(PM->BM)": None,  # has its own brand column
    "비건인증": None,                  # has its own brand column
    "공통(SKIN1004)": "SKIN1004",
    "제품 사용 루틴": "SKIN1004",
    "센텔라": "SKIN1004",
    "히알루-시카": "SKIN1004",
    "톤브라이트닝": "SKIN1004",
    "포어마이징": "SKIN1004",
    "티트리카": "SKIN1004",
    "프로바이오시카": "SKIN1004",
    "랩인네이처": "SKIN1004",
    "제품CS(COMMONLABS)": "COMMONLABS",
    "제품CS(ZOMBIE BEAUTY)": "ZOMBIE BEAUTY",
}

# ── Tab → product line mapping ──
_TAB_LINE_MAP = {
    "센텔라": "센텔라",
    "히알루-시카": "히알루-시카",
    "톤브라이트닝": "톤브라이트닝",
    "포어마이징": "포어마이징",
    "티트리카": "티트리카",
    "프로바이오시카": "프로바이오시카",
    "랩인네이처": "랩인네이처",
}

# Korean aliases for English brand names (used in search scoring)
_BRAND_ALIASES = {
    "commonlabs": ["커먼랩스", "커먼랩"],
    "zombie beauty": ["좀비뷰티", "좀비 뷰티"],
    "skin1004": ["스킨1004", "스킨일공공사"],
}

# Header keywords for identifying columns across varying tab structures
_QUESTION_HEADERS = {"질문", "질문 ", "문의내용", "q", "question", "문의"}
_ANSWER_HEADERS = {"답변", "답변내용", "a", "answer", "회신"}
_PRODUCT_HEADERS = {"제품명", "제품명 ", "제품", "product", "product name_kor", "품목"}
_BRAND_HEADERS = {"브랜드", "brand"}
_LINE_HEADERS = {"라인", "라인명", "line"}
_CATEGORY_HEADERS = {"카테고리", "category", "분류", "유형"}

# Max rows to scan for the header row (title/instruction rows come before)
_HEADER_SCAN_LIMIT = 10


def _find_col_index(headers: List[str], target_set: set) -> int:
    """Find column index matching any keyword in target_set. Returns -1 if not found."""
    for i, h in enumerate(headers):
        if h.strip().lower() in target_set:
            return i
    return -1


def _safe_get(row: List[str], idx: int) -> str:
    """Safely get a cell value by index."""
    if idx < 0 or idx >= len(row):
        return ""
    return row[idx].strip()


def _find_header_row(rows: List[List[str]]) -> int:
    """Scan the first N rows to find the header row containing Q&A column names.

    Most tabs have title/instruction rows before the actual header.
    Returns the row index of the header, or -1 if not found.
    """
    limit = min(_HEADER_SCAN_LIMIT, len(rows))
    for i in range(limit):
        if not rows[i]:
            continue
        cells = [c.strip().lower() for c in rows[i]]
        has_q = any(c in _QUESTION_HEADERS for c in cells)
        has_a = any(c in _ANSWER_HEADERS for c in cells)
        if has_q or has_a:
            return i
    return -1


def _normalize_tab(tab_name: str, rows: List[List[str]]) -> List[Dict[str, str]]:
    """Normalize a single tab's rows into unified Q&A dicts.

    Scans rows 0-9 to locate the actual header row (skipping title/instruction rows).
    Falls back to freeform extraction if no header row is found.
    """
    if not rows:
        return []

    default_brand = _TAB_BRAND_MAP.get(tab_name, "SKIN1004")
    default_line = _TAB_LINE_MAP.get(tab_name, "")

    # Special case: 비건인증 tab — certification list, not Q&A
    if "비건인증" in tab_name:
        return _normalize_vegan_tab(rows)

    # Scan for header row
    header_idx = _find_header_row(rows)

    # No header found → freeform text tab
    if header_idx < 0:
        return _normalize_freeform_tab(tab_name, rows, default_brand, default_line)

    # Standard Q&A tab
    headers = [h.strip().lower() for h in rows[header_idx]]

    q_idx = _find_col_index(headers, _QUESTION_HEADERS)
    a_idx = _find_col_index(headers, _ANSWER_HEADERS)
    p_idx = _find_col_index(headers, _PRODUCT_HEADERS)
    b_idx = _find_col_index(headers, _BRAND_HEADERS)
    l_idx = _find_col_index(headers, _LINE_HEADERS)
    c_idx = _find_col_index(headers, _CATEGORY_HEADERS)

    results = []
    for row in rows[header_idx + 1:]:  # skip everything up to and including header
        question = _safe_get(row, q_idx) if q_idx >= 0 else ""
        answer = _safe_get(row, a_idx) if a_idx >= 0 else ""
        if not question and not answer:
            continue

        brand = _safe_get(row, b_idx) if b_idx >= 0 else (default_brand or "")
        line = _safe_get(row, l_idx) if l_idx >= 0 else default_line
        product = _safe_get(row, p_idx) if p_idx >= 0 else ""
        category = _safe_get(row, c_idx) if c_idx >= 0 else ""

        results.append({
            "tab": tab_name,
            "brand": brand,
            "line": line,
            "product": product,
            "category": category,
            "question": question,
            "answer": answer,
        })

    return results


def _normalize_vegan_tab(rows: List[List[str]]) -> List[Dict[str, str]]:
    """Normalize 비건인증 tab into Q&A format.

    Scans for the header row containing brand/product columns.
    Converts each product's certification status into a question-answer pair.
    """
    if len(rows) < 2:
        return []

    # Find header row (look for "brand" or "product name_kor" etc.)
    header_idx = -1
    for i in range(min(_HEADER_SCAN_LIMIT, len(rows))):
        if not rows[i]:
            continue
        cells = [c.strip().lower() for c in rows[i]]
        if any(c in _BRAND_HEADERS or c in _PRODUCT_HEADERS for c in cells):
            header_idx = i
            break

    if header_idx < 0:
        return []

    headers = [h.strip().lower() for h in rows[header_idx]]
    b_idx = _find_col_index(headers, _BRAND_HEADERS)
    p_idx = _find_col_index(headers, _PRODUCT_HEADERS)

    # Remaining non-empty columns are certification statuses
    cert_indices = []
    for i, h in enumerate(headers):
        if i not in (b_idx, p_idx) and h and h not in ("capacity", "product name_eng"):
            cert_indices.append((i, rows[header_idx][i].strip()))

    results = []
    for row in rows[header_idx + 1:]:
        brand = _safe_get(row, b_idx) if b_idx >= 0 else ""
        product = _safe_get(row, p_idx) if p_idx >= 0 else ""
        if not product:
            continue

        # Build certification answer
        certs = []
        for ci, cert_name in cert_indices:
            val = _safe_get(row, ci)
            if val:
                certs.append(f"{cert_name}: {val}")

        cert_answer = ", ".join(certs) if certs else "정보 없음"

        results.append({
            "tab": "비건인증",
            "brand": brand,
            "line": "",
            "product": product,
            "category": "비건인증",
            "question": f"{product} 비건 인증 상태는?",
            "answer": cert_answer,
        })

    return results


def _normalize_freeform_tab(
    tab_name: str,
    rows: List[List[str]],
    brand: str,
    line: str,
) -> List[Dict[str, str]]:
    """Normalize a free-form text tab (no Q&A columns) into a single Q&A entry."""
    # Concatenate all non-empty cells as content
    all_text = []
    for row in rows:
        for cell in row:
            cell = cell.strip()
            if cell:
                all_text.append(cell)

    if not all_text:
        return []

    content = "\n".join(all_text)
    return [{
        "tab": tab_name,
        "brand": brand or "SKIN1004",
        "line": line,
        "product": "",
        "category": tab_name,
        "question": f"{tab_name} 정보",
        "answer": content,
    }]


async def load_all_sheets() -> List[Dict[str, str]]:
    """Load all 13 tabs from the CS spreadsheet and normalize into Q&A list.

    Uses service account credentials (same as other Google API calls).
    Runs synchronous Sheets API in a thread to avoid blocking the event loop.
    """
    settings = get_settings()
    spreadsheet_id = settings.cs_spreadsheet_id
    if not spreadsheet_id:
        logger.warning("cs_spreadsheet_id_not_set")
        return []

    def _load_sync() -> List[Dict[str, str]]:
        creds = Credentials.from_service_account_file(
            settings.google_application_credentials,
            scopes=_SCOPES,
        )
        service = build("sheets", "v4", credentials=creds)

        # Step 1: Get all tab names
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        tab_names = [s["properties"]["title"] for s in meta.get("sheets", [])]
        logger.info("cs_tabs_found", count=len(tab_names), tabs=tab_names)

        # Step 2: Batch-read all tabs in one API call
        ranges = [f"'{name}'!A:Z" for name in tab_names]
        batch_result = (
            service.spreadsheets()
            .values()
            .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges)
            .execute()
        )

        # Step 3: Normalize each tab
        all_qa = []
        value_ranges = batch_result.get("valueRanges", [])
        for i, vr in enumerate(value_ranges):
            tab_name = tab_names[i] if i < len(tab_names) else f"Tab_{i}"
            rows = vr.get("values", [])
            normalized = _normalize_tab(tab_name, rows)
            all_qa.extend(normalized)
            logger.info("cs_tab_loaded", tab=tab_name, rows=len(rows), qa_count=len(normalized))

        return all_qa

    return await asyncio.to_thread(_load_sync)


async def warmup() -> int:
    """Load CS data into module-level cache. Called at server startup.

    Returns:
        Number of Q&A entries loaded.
    """
    global _qa_cache, _cache_loaded, _loaded_at
    try:
        _qa_cache = await load_all_sheets()
        _cache_loaded = True
        _loaded_at = _time.time()
        logger.info("cs_cache_loaded", total_qa=len(_qa_cache))
        return len(_qa_cache)
    except Exception as e:
        logger.error("cs_warmup_failed", error=str(e))
        _cache_loaded = False
        return 0


async def refresh() -> int:
    """시트를 다시 읽어 캐시를 **바꿔 끼운다** (`cs_cache_hourly` 가 매시 부른다).

    ⛔ `warmup()` 을 그대로 쓰지 않는 이유가 둘이다:
      1. 실패하면 `warmup()` 은 `_cache_loaded=False` 로 내린다 → 다음 **사용자
         질문**이 재로딩(수 초)을 떠안는다. 갱신 실패가 사용자 지연이 되면 안 된다
      2. 빈 목록으로 바꿔 끼우면 *"CS 데이터베이스가 비어있습니다"* 가 나간다.
         **옛 자료라도 있는 편이 낫다** — 그래서 비면 바꾸지 않는다
    """
    global _qa_cache, _cache_loaded, _loaded_at
    try:
        fresh = await load_all_sheets()
    except Exception as e:
        logger.warning("cs_cache_refresh_failed", error=str(e)[:200],
                       kept=len(_qa_cache))
        return -1
    if not fresh:
        # ⚠️ 0건은 성공이 아니다 — 권한이 끊기거나 탭 이름이 바뀌면 이렇게 온다
        logger.warning("cs_cache_refresh_empty", kept=len(_qa_cache))
        return -1
    before = len(_qa_cache)
    _qa_cache = fresh
    _cache_loaded = True
    _loaded_at = _time.time()
    if before != len(fresh):
        logger.info("cs_cache_refreshed", before=before, after=len(fresh))
    return len(fresh)


def status() -> Dict[str, object]:
    """자가 점검·상태 화면용 — 캐시가 실제로 최신인가."""
    age = (_time.time() - _loaded_at) if _loaded_at else None
    return {
        "loaded": _cache_loaded,
        "count": len(_qa_cache),
        "age_seconds": None if age is None else int(age),
    }


def _tokenize(text: str) -> set:
    """Simple Korean+English tokenizer: split on whitespace and punctuation."""
    text = text.lower()
    # Remove punctuation except Korean characters
    tokens = re.findall(r'[가-힣a-z0-9]+', text)
    return set(tokens)


def _word_overlap_score(query_tokens: set, text: str) -> float:
    """Compute word overlap score between query tokens and text."""
    if not query_tokens or not text:
        return 0.0
    text_tokens = _tokenize(text)
    if not text_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    # Jaccard-like: overlap / query_size (favor matching more query terms)
    return len(overlap) / len(query_tokens)


def _log_knowledge_gap(query: str) -> None:
    """CS에서 답 못 찾은 질문을 knowledge_gaps 테이블에 저장 (fire-and-forget, sync)."""
    try:
        from app.db.mariadb import execute
        execute(
            "INSERT INTO knowledge_gaps (question, route) VALUES (%s, 'cs')",
            (query[:500],),
        )
    except Exception:
        pass


def search_qa(query: str, top_k: int = 10) -> List[Dict[str, str]]:
    """Search the cached Q&A data for entries matching the query.

    Search strategy:
    1. Extract keywords from query (product names, line names, brand names)
    2. Filter by keyword matches on product/line/brand/category fields
    3. Score remaining by question text similarity (word overlap)
    4. Return top-k results sorted by score

    Args:
        query: User's question.
        top_k: Maximum number of results to return.

    Returns:
        List of matching Q&A dicts, sorted by relevance.
    """
    if not _qa_cache:
        return []

    q_lower = query.lower()
    q_tokens = _tokenize(query)

    # ⛔ 질문이 라인을 지목했으면 **다른 라인 자료로 답하지 않는다** (붐따 #111).
    #    "히알루 테카 라인 제품 정보" 가 '라인·제품·정보' 겹침만으로 히알루시카 Q&A 를
    #    최상위로 올렸고, 답변은 히알루시카를 설명하면서 바꿔치기했다는 말을 하지 않았다.
    #    프롬프트 규칙("다른 제품 정보를 제공하지 마세요")은 이미 있었는데도 났다 —
    #    보증은 코드가 한다. 지목한 라인 자료가 없으면 **0건**이 맞다:
    #    `run()` 이 knowledge_gap 을 남기고 "찾지 못했습니다" 라고 답한다.
    asked_lines = product_lines.mentioned(query)

    scored = []
    for qa in _qa_cache:
        if asked_lines:
            row_lines = product_lines.mentioned(
                f"{qa['line']} {qa['product']} {qa['question']}")
            if not (row_lines & asked_lines):
                continue
        score = 0.0

        # Exact product/line/brand match in query → high boost
        product = qa["product"].lower()
        line = qa["line"].lower()
        brand = qa["brand"].lower()
        category = qa["category"].lower()

        if product and product in q_lower:
            score += 3.0
        if line and line in q_lower:
            score += 2.0
        if brand and brand in q_lower:
            score += 1.0
        elif brand:
            # Check Korean aliases for English brand names
            aliases = _BRAND_ALIASES.get(brand, [])
            if any(alias in q_lower for alias in aliases):
                score += 1.0
        if category and category in q_lower:
            score += 1.5

        # Question text similarity
        q_sim = _word_overlap_score(q_tokens, qa["question"])
        score += q_sim * 2.0

        # Answer text also may contain relevant keywords
        a_sim = _word_overlap_score(q_tokens, qa["answer"])
        score += a_sim * 0.5

        if score > 0:
            scored.append((score, qa))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    return [qa for _, qa in scored[:top_k]]


def _format_qa_context(matched_qas: List[Dict[str, str]]) -> str:
    """Format matched Q&A entries as context for LLM prompt."""
    parts = []
    for i, qa in enumerate(matched_qas, 1):
        meta_parts = []
        if qa["brand"]:
            meta_parts.append(f"브랜드: {qa['brand']}")
        if qa["line"]:
            meta_parts.append(f"라인: {qa['line']}")
        if qa["product"]:
            meta_parts.append(f"제품: {qa['product']}")
        if qa["category"]:
            meta_parts.append(f"카테고리: {qa['category']}")
        meta = " | ".join(meta_parts) if meta_parts else qa["tab"]

        parts.append(
            f"[{i}] ({meta})\n"
            f"Q: {qa['question']}\n"
            f"A: {qa['answer']}"
        )
    return "\n\n".join(parts)


async def run(query: str, model_type: str = "gemini") -> str:
    """Main entry point: search CS DB and generate answer.

    Args:
        query: User's CS-related question.
        model_type: LLM to use for answer synthesis.

    Returns:
        Generated answer string.
    """
    global _qa_cache, _cache_loaded

    # Lazy load if cache not populated (e.g., warmup failed)
    if not _cache_loaded:
        logger.info("cs_lazy_loading")
        await warmup()

    if not _qa_cache:
        return ("CS 데이터베이스가 비어있습니다. "
                "스프레드시트 설정을 확인해주세요.")

    # Search for relevant Q&A
    matched = search_qa(query, top_k=10)

    if not matched:
        # No matches — log knowledge gap for autonomous growth tracking
        _log_knowledge_gap(query)
        return await _generate_no_match_answer(query, model_type)

    # Format context and generate answer
    context = _format_qa_context(matched)
    return await _generate_answer(query, context, len(matched), model_type)


# ⛔ 오케스트레이터는 원문이 아니라 대화 맥락을 감싼 덩어리를 넘긴다
#    (스트리밍·비스트리밍 **양쪽 다**). 이 표식 뒤가 사용자가 지금 물은 것이다.
CURRENT_QUESTION_MARKER = "[현재 질문]"


def _current_question(query: str) -> str:
    """맥락 덩어리에서 **지금 물은 것**만 떼어 낸다. 표식이 없으면 원문 그대로.

    ⛔ **왜 필요한가** (2026-09-09 프로덕션 실측). `extract()` 는 문서 순서대로
       8개만 뽑는데 현재 질문이 맨 뒤에 온다 — 맥락이 상한을 다 먹으면
       정작 물어본 낱말이 검색어에 **들어오지도 못한다**:

           넘어온 것: "[이전 대화] 센텔라 앰플 사용법 … [현재 질문] 테카 앰플 정보좀"
           검색어    : ['이전','대화','사용자','센텔라','앰플','사용법','셀라','마다가스카르']
                       ← '테카' 가 없다

       그래서 "테카 앰플" 을 물었는데 센텔라·티트리카·포어마이징 앰플 목록이
       나가고 *"등록되어 있지 않습니다"* 라고 답했다.
    ⚠️ `search("테카 앰플 정보좀")` 을 직접 부르면 **맞게 나온다** — 실사용
       경로는 그 문자열을 부르지 않는다. 고쳤다고 확신하게 만드는 모양이라
       특히 위험하다. 검증은 이 함수를 지나는 경로로 할 것.
    """
    text = query or ""
    i = text.rfind(CURRENT_QUESTION_MARKER)
    if i < 0:
        return text
    return text[i + len(CURRENT_QUESTION_MARKER):].strip() or text


def _product_info_block(query: str) -> str:
    """제품정보(스펙) 블록 — **Q&A 와 별개 소스**다 (2026-09-04 사용자 지시:
    *"제품 q&A는 따로이고, 제품정보는 cs데이터에 들어가야함"*).

    Q&A 는 *문의 대응 기록*이고 제품정보는 *제품 스펙*이다. 한 덩어리로 섞으면
    답변이 어느 쪽을 근거로 말하는지 사라진다 — 블록을 나눠 넣는다.
    ⚠️ 실패해도 CS 답변이 죽으면 안 된다 — 비면 그냥 빠진다.
    """
    asked = _current_question(query)
    try:
        from app.core.product_info import search as _pi_search
        rows = _pi_search(asked, limit=4)
        # ⚠️ 맥락을 버리지는 않는다 — "그럼 크림은?" 같은 후속 발화는 그 자체로
        #    라인을 모른다. 현재 질문으로 못 찾을 때만 덩어리 전체를 본다.
        if not rows and asked != query:
            rows = _pi_search(query, limit=4)
    except Exception as e:
        logger.warning("product_info_block_failed", error=str(e)[:140])
        return ""
    if not rows:
        return ""
    parts = []
    for r in rows:
        body = str(r.get("body") or "")[:1400]
        parts.append(f"- [{r.get('line')}] {r.get('product')}\n{body}")
    return ("\n\n## 제품정보 (제품 스펙 — 사내 노션 `스킨1004 전제품 한 눈에 파악하기`)\n"
            + "\n\n".join(parts))


def _build_answer_prompt(query: str, context: str, match_count: int) -> str:
    """Build the CS answer-synthesis prompt (shared by run() and run_stream())."""
    return f"""당신은 SKIN1004/COMMONLABS/ZOMBIE BEAUTY의 CS(고객상담) 전문 AI입니다.
고객의 질문에 전문적이면서도 친절하게, 구조화된 형태로 답변하세요.

아래는 사내 CS 데이터베이스에서 검색된 Q&A 자료입니다.

## CS 데이터베이스 검색 결과 ({match_count}건)
{context}
{_product_info_block(query)}

⚠️ **두 자료는 성격이 다릅니다.** `CS 데이터베이스`는 실제 문의 대응 기록이고,
`제품정보`는 제품 스펙(사용법·성분·함량·피부타입)입니다. 제품의 성분·사용법을 물으면
**제품정보**를 근거로 답하고, 문의 처리 방법·정책은 **Q&A**를 근거로 답하세요.
⛔ 두 자료에 없는 수치(함량·PPM 등)를 지어내지 마세요.

## 고객 질문
{query}

## 답변 형식 (반드시 아래 구조를 따르세요)

### 🧴 [질문 주제에 맞는 제목]

#### 답변
[질문에 대한 핵심 답변. 1-3문장으로 결론부터 제시. 중요 키워드는 **굵게**]

#### 상세 정보
[관련 상세 내용을 구조화하여 정리]
- 성분/효능 관련: 주요 성분과 기능을 bullet으로
- 사용법 관련: 단계별 순서 (1. → 2. → 3.)
- 비교 관련: 마크다운 표로 정리
- 내용이 간단하면 이 섹션 생략 가능

#### 참고 사항
[주의사항, 팁, 관련 제품 추천 등. CS DB에 있는 내용만. 없으면 생략]

---
*출처: CS 제품 Q&A 데이터베이스*

> 💡 **이런 것도 물어보세요**
> - [관련 제품/성분에 대한 후속 질문]
> - [사용법이나 루틴 관련 질문]
> - [다른 피부 타입/고민 관련 질문]

## 답변 규칙
1. CS 데이터베이스의 내용을 기반으로 정확하게 답변하세요.
2. 데이터에 없는 내용은 추측하지 마세요. "해당 정보가 CS DB에 없습니다"라고 안내하세요.
3. 여러 관련 Q&A가 있으면 종합하여 하나의 완성된 답변으로 정리하세요.
4. 제품명, 성분, 사용법 등 구체적인 정보를 **굵게** 표시하며 포함하세요.
5. 전문적이면서도 친절한 톤으로 답변하세요.
6. ⚠️ **후속 질문 제안 필수**: 답변 끝에 반드시 "💡 이런 것도 물어보세요" 블록을 포함하세요. 이 블록이 없으면 답변이 불완전합니다.

## ⚠️ 질문-답변 정합성 (최우선)
7. **사용자의 원래 질문에 정확히 답변하세요.** 질문과 다른 내용으로 답변을 대체하지 마세요.
8. 질문한 제품/브랜드와 다른 제품의 정보를 제공하지 마세요.
9. 검색된 Q&A가 질문과 관련 없으면 "해당 질문에 대한 CS 데이터를 찾지 못했습니다"라고 솔직히 답하세요.
10. 질문에 없는 내용을 덧붙이거나 주제를 바꾸지 마세요.

## ⛔ 대외비 규칙 (절대 준수)
11. 답변 내용에 "대외비", "비공개", "내부 참고용", "공개 불가" 등의 키워드가 포함된 경우, 답변 마지막에 반드시 아래 문구를 추가하세요:
    `⚠️ 본 정보는 사내 대외비입니다. 외부(고객, 거래처, SNS 등)에 공유하지 마세요.`
12. 특정 성분 함량(ppm, %) 관련 답변에도 동일하게 대외비 안내를 추가하세요."""


async def _generate_answer(
    query: str,
    context: str,
    match_count: int,
    model_type: str,
) -> str:
    """Generate a synthesized answer from matched Q&A entries."""
    # Use Flash for CS — simple Q&A synthesis doesn't need Pro/Claude
    llm = get_flash_client()
    prompt = _build_answer_prompt(query, context, match_count)

    try:
        answer = llm.generate(prompt, temperature=0.3)
        return answer
    except Exception as e:
        logger.error("cs_generate_failed", error=str(e))
        # Fallback: return raw matched Q&A
        return f"CS DB 검색 결과:\n\n{context}"


async def run_stream(query: str, model_type: str = "gemini"):
    """Streaming variant of run() — yields answer text chunks.

    Mirrors run()'s search/no-match logic exactly; only the final answer
    synthesis step streams instead of blocking.
    """
    global _qa_cache, _cache_loaded

    if not _cache_loaded:
        await warmup()

    if not _qa_cache:
        yield "CS 데이터베이스가 비어있습니다. 스프레드시트 설정을 확인해주세요."
        return

    matched = search_qa(query, top_k=10)

    if not matched:
        _log_knowledge_gap(query)
        yield await _generate_no_match_answer(query, model_type)
        return

    context = _format_qa_context(matched)
    prompt = _build_answer_prompt(query, context, len(matched))
    llm = get_flash_client()

    from app.core.stream_bridge import stream_sync_generator
    try:
        async for chunk in stream_sync_generator(lambda: llm.generate_stream(prompt, temperature=0.3)):
            yield chunk
    except Exception as e:
        logger.error("cs_generate_stream_failed", error=str(e))
        yield f"CS DB 검색 결과:\n\n{context}"


async def _generate_no_match_answer(query: str, model_type: str) -> str:
    """Generate a helpful response when no Q&A matches are found."""
    flash = get_flash_client()

    # Show available categories/products for guidance
    brands = set()
    lines = set()
    products = set()
    for qa in _qa_cache[:200]:  # sample
        if qa["brand"]:
            brands.add(qa["brand"])
        if qa["line"]:
            lines.add(qa["line"])
        if qa["product"] and len(qa["product"]) < 30:
            products.add(qa["product"])

    available = (
        f"브랜드: {', '.join(sorted(brands))}\n"
        f"라인: {', '.join(sorted(lines))}\n"
        f"제품 예시: {', '.join(sorted(list(products)[:15]))}"
    )

    prompt = f"""고객이 CS 관련 질문을 했으나, CS 데이터베이스에서 관련 정보를 찾지 못했습니다.

고객 질문: {query}

현재 CS DB에 등록된 정보:
{available}

친절하게 안내해주세요:
1. 질문하신 내용에 대한 정확한 CS 정보를 찾지 못했다고 안내
2. 위 목록에서 관련 제품이나 라인을 추천
3. 질문을 더 구체적으로 해주시면 도움드릴 수 있다고 안내
한국어로 답변하세요."""

    try:
        return flash.generate(prompt, temperature=0.3)
    except Exception as e:
        logger.error("cs_no_match_generate_failed", error=str(e))
        return (
            "### 🧴 CS 정보 조회 결과\n\n"
            "질문하신 내용과 관련된 CS 정보를 찾지 못했습니다.\n\n"
            f"#### 현재 CS DB에 등록된 정보\n{available}\n\n"
            "---\n\n"
            "> 💡 **이런 식으로 질문해 보세요**\n"
            "> - \"센텔라 앰플 성분 알려줘\"\n"
            "> - \"히알루시카 크림 사용법\"\n"
            "> - \"비건 인증 제품 목록\""
        )
