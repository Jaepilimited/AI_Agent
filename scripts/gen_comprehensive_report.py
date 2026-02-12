"""Generate comprehensive combined report PDF.

Combines:
1. Test Results Summary (from test_report_comprehensive_2026-02-12.md)
2. QA Detail — All 112 questions and answers
3. Improvement Log (update history v5.0 → v6.2)
"""
import re
import os
from fpdf import FPDF


class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('malgun', '', 8)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', align='C')


EMOJI_RE = re.compile(
    '['
    '\U0001F300-\U0001F9FF'
    '\U00002702-\U000027B0'
    '\U0000FE0F\U00002139\U000026A0\U00002705\U00002611'
    '\U0001F5D3\u30FC'
    ']+', re.UNICODE
)


def strip_emoji(text: str) -> str:
    return EMOJI_RE.sub('', text)


def clean(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = strip_emoji(text)
    return text.strip()


# ── Markdown rendering helpers ──

def render_md_lines(pdf: FPDF, lines: list[str]):
    """Render markdown lines to PDF."""
    in_table = False
    table_rows = []
    in_code = False

    def flush_table():
        nonlocal table_rows, in_table
        if not table_rows:
            in_table = False
            return
        col_count = len(table_rows[0])
        page_w = pdf.w - pdf.l_margin - pdf.r_margin
        col_w = page_w / col_count if col_count > 0 else page_w
        if col_w < 12:
            col_w = 12
        max_chars = int(col_w / 2.2)
        if max_chars < 8:
            max_chars = 8
        for ri, row in enumerate(table_rows):
            if ri == 0:
                pdf.set_font('malgun', 'B', 7)
                pdf.set_fill_color(230, 235, 245)
                for cell in row:
                    pdf.cell(col_w, 5, clean(cell)[:max_chars], border=1, fill=True)
                pdf.ln()
            else:
                pdf.set_font('malgun', '', 7)
                for cell in row:
                    pdf.cell(col_w, 4.5, clean(cell)[:max_chars], border=1)
                pdf.ln()
        table_rows = []
        in_table = False
        pdf.ln(1)

    for line in lines:
        line = line.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue

        if '|' in line and line.strip().startswith('|'):
            cells = [c.strip() for c in line.split('|')[1:-1]]
            if all(re.match(r'^[-:]+$', c) for c in cells):
                continue
            in_table = True
            table_rows.append(cells)
            continue
        elif in_table:
            flush_table()

        if not line.strip():
            pdf.ln(2)
            continue

        pdf.set_x(pdf.l_margin)
        stripped = strip_emoji(line)

        if stripped.startswith('# ') and not stripped.startswith('## '):
            pdf.set_font('malgun', 'B', 14)
            pdf.cell(0, 9, clean(stripped[2:]), new_x='LMARGIN', new_y='NEXT')
            pdf.ln(2)
        elif stripped.startswith('## '):
            pdf.ln(3)
            pdf.set_font('malgun', 'B', 12)
            pdf.cell(0, 7, clean(stripped[3:]), new_x='LMARGIN', new_y='NEXT')
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(2)
        elif stripped.startswith('### '):
            pdf.ln(2)
            pdf.set_font('malgun', 'B', 10)
            pdf.cell(0, 6, clean(stripped[4:]), new_x='LMARGIN', new_y='NEXT')
            pdf.ln(1)
        elif stripped.startswith('#### '):
            pdf.set_font('malgun', 'B', 9)
            pdf.cell(0, 5, clean(stripped[5:]), new_x='LMARGIN', new_y='NEXT')
            pdf.ln(1)
        elif stripped.startswith('> '):
            pdf.set_font('malgun', '', 8)
            pdf.set_text_color(80, 80, 80)
            pdf.set_x(pdf.l_margin + 5)
            pdf.multi_cell(0, 4, clean(stripped[2:]))
            pdf.set_text_color(0, 0, 0)
        elif stripped.lstrip().startswith('- ') or stripped.lstrip().startswith('* '):
            indent = len(stripped) - len(stripped.lstrip())
            indent_mm = min(indent * 1.5, 20)
            pdf.set_font('malgun', '', 8)
            text = clean(stripped.lstrip().lstrip('-* '))
            pdf.set_x(pdf.l_margin + indent_mm)
            pdf.multi_cell(0, 4, '  - ' + text)
        elif re.match(r'^\s*\d+\.', stripped):
            pdf.set_font('malgun', '', 8)
            pdf.multi_cell(0, 4, clean(stripped.strip()))
        elif stripped.startswith('---'):
            pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
            pdf.ln(3)
        else:
            pdf.set_font('malgun', '', 8)
            pdf.multi_cell(0, 4, clean(stripped))

    if in_table:
        flush_table()


def render_answer(pdf: FPDF, answer: str):
    """Render a single Q&A answer block."""
    if not answer:
        pdf.set_font('malgun', '', 8)
        pdf.set_text_color(150, 150, 150)
        pdf.multi_cell(0, 4, '(No answer)')
        pdf.set_text_color(0, 0, 0)
        return
    render_md_lines(pdf, answer.split('\n'))


# ── Parse test result files ──

def parse_result_file(filepath: str) -> list[dict]:
    """Parse test result .txt file into list of entries."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    entries = []
    blocks = re.split(r'={50,}', content)

    for block in blocks:
        block = block.strip()
        id_match = re.match(r'^\[([A-Z0-9\-]+)\](.*)$', block, re.MULTILINE)
        if not id_match:
            if block.startswith('SUMMARY'):
                break
            continue

        test_id = id_match.group(1)
        category_line = id_match.group(2).strip()

        q_match = re.search(r'^Q:\s*(.+)$', block, re.MULTILINE)
        expected_match = re.search(r'^Expected:\s*(.+)$', block, re.MULTILINE)
        status_match = re.search(r'^Status:\s*(.+)$', block, re.MULTILINE)
        fix_match = re.search(r'^Fix:\s*(.+)$', block, re.MULTILINE)

        question = q_match.group(1).strip() if q_match else ''
        expected = expected_match.group(1).strip() if expected_match else ''
        status_line = status_match.group(1).strip() if status_match else ''
        fix_desc = fix_match.group(1).strip() if fix_match else ''

        time_match = re.search(r'([\d.]+)s', status_line)
        status_val = 'OK' if 'OK' in status_line else ('EXCEPTION' if 'EXCEPTION' in status_line else 'OTHER')

        answer = ''
        underline_pos = block.find('_' * 20)
        if underline_pos < 0:
            underline_pos = block.find('\u2500' * 20)
        if underline_pos >= 0:
            answer = block[underline_pos:].strip()
            answer = re.sub(r'^[_\u2500]+\s*', '', answer).strip()

        entries.append({
            'id': test_id,
            'category': category_line,
            'question': question,
            'expected': expected,
            'status': status_val,
            'status_line': status_line,
            'time': float(time_match.group(1)) if time_match else 0,
            'fix': fix_desc,
            'answer': answer,
        })

    return entries


# ── Main ──

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_font('malgun', '', 'C:/Windows/Fonts/malgun.ttf')
    pdf.add_font('malgun', 'B', 'C:/Windows/Fonts/malgunbd.ttf')
    pdf.set_auto_page_break(auto=True, margin=15)

    # ═══════════════════════════════════════
    # COVER PAGE
    # ═══════════════════════════════════════
    pdf.add_page()
    pdf.ln(30)
    pdf.set_font('malgun', 'B', 24)
    pdf.cell(0, 15, 'SKIN1004 AI Agent', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(5)
    pdf.set_font('malgun', 'B', 18)
    pdf.cell(0, 12, strip_emoji('종합 리포트'), align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(5)
    pdf.set_font('malgun', '', 11)
    pdf.cell(0, 8, strip_emoji('테스트 결과 + QA 상세 + 개선 이력'), align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(8)
    pdf.set_font('malgun', '', 10)
    pdf.cell(0, 7, 'Date: 2026-02-12', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 7, 'Version: v6.2.0 (Notion v6.2 / GWS v4.2)', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 7, 'DB Team / Data Analytics', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(15)

    # Key metrics on cover
    pdf.set_font('malgun', 'B', 11)
    pdf.cell(0, 8, strip_emoji('핵심 지표'), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(2)
    metrics = [
        ['항목', '값'],
        ['총 테스트', '167개 (R1-R3 112개 + R4 55개)'],
        ['R1-R3 성공률', '92.0% (103/112)'],
        ['R4 성공률', '100.0% (55/55)'],
        ['실질적 버그', '5건 발견 -> v6.1/v4.1에서 전수 수정 -> 0건'],
        ['BigQuery 평균 응답', 'R1-R3: 18.5s / R4: 21.3s'],
        ['Notion 평균 응답', 'R1-R3: 36.7s / R4: 28.4s'],
        ['GWS 평균 응답', 'R1-R3: ~20s / R4: 19.0s'],
        ['WARN/FAIL (R4)', '0건 / 0건'],
    ]
    col_widths = [50, 120]
    for ri, row in enumerate(metrics):
        if ri == 0:
            pdf.set_font('malgun', 'B', 9)
            pdf.set_fill_color(230, 235, 245)
        else:
            pdf.set_font('malgun', '', 9)
        for ci, cell in enumerate(row):
            pdf.cell(col_widths[ci], 6, clean(cell), border=1,
                     fill=(ri == 0))
        pdf.ln()

    # ═══════════════════════════════════════
    # PART 1: TEST RESULTS SUMMARY
    # ═══════════════════════════════════════
    pdf.add_page()
    pdf.set_font('malgun', 'B', 16)
    pdf.cell(0, 10, strip_emoji('Part 1: 테스트 결과 요약'), new_x='LMARGIN', new_y='NEXT')
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)

    test_report = os.path.join(base_dir, 'docs', 'test_report_comprehensive_2026-02-12.md')
    if os.path.exists(test_report):
        with open(test_report, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        render_md_lines(pdf, [l.rstrip() for l in lines])
    else:
        pdf.set_font('malgun', '', 10)
        pdf.multi_cell(0, 6, '(test_report_comprehensive_2026-02-12.md not found)')

    # ═══════════════════════════════════════
    # PART 2: QA DETAIL (All Questions & Answers)
    # ═══════════════════════════════════════
    pdf.add_page()
    pdf.set_font('malgun', 'B', 16)
    pdf.cell(0, 10, strip_emoji('Part 2: QA 질문/답변 상세'), new_x='LMARGIN', new_y='NEXT')
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)

    test_files = [
        ('test_team_bigquery_result.txt', 'Round 1 - BigQuery (20)'),
        ('test_team_notion_result.txt', 'Round 1 - Notion (20)'),
        ('test_team_gws_result.txt', 'Round 1 - GWS (15)'),
        ('test_team_r2_bigquery_result.txt', 'Round 2 - BigQuery (15)'),
        ('test_team_r2_notion_result.txt', 'Round 2 - Notion (12)'),
        ('test_team_r2_gws_result.txt', 'Round 2 - GWS (10)'),
        ('test_team_r3_edge_result.txt', 'Round 3 - Edge Cases (15)'),
        ('test_regression_result.txt', strip_emoji('회귀 테스트 (5)')),
        ('test_round4_diverse_result.txt', 'Round 4 - Diverse Variables (55)'),
    ]

    total_entries = 0
    for filename, section_title in test_files:
        filepath = os.path.join(base_dir, filename)
        if not os.path.exists(filepath):
            continue

        entries = parse_result_file(filepath)
        if not entries:
            continue

        # Section header
        pdf.ln(3)
        pdf.set_font('malgun', 'B', 12)
        pdf.set_fill_color(50, 60, 80)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 8, f'  {section_title} — {len(entries)} entries', fill=True,
                 new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

        for entry in entries:
            total_entries += 1

            # Entry header
            status_color = (0, 128, 0) if entry['status'] == 'OK' else (200, 0, 0)
            pdf.set_font('malgun', 'B', 9)
            pdf.set_fill_color(240, 242, 248)
            header = f"[{entry['id']}] {entry['question'][:80]}"
            pdf.cell(0, 6, clean(header), fill=True, new_x='LMARGIN', new_y='NEXT')

            # Status line
            pdf.set_font('malgun', '', 8)
            pdf.set_text_color(*status_color)
            status_text = f"Status: {strip_emoji(entry['status_line'][:100])}"
            pdf.cell(0, 5, clean(status_text), new_x='LMARGIN', new_y='NEXT')
            pdf.set_text_color(0, 0, 0)

            if entry['fix']:
                pdf.set_font('malgun', '', 8)
                pdf.set_text_color(0, 0, 180)
                pdf.cell(0, 5, f"Fix: {clean(entry['fix'][:100])}", new_x='LMARGIN', new_y='NEXT')
                pdf.set_text_color(0, 0, 0)

            # Answer
            pdf.ln(1)
            render_answer(pdf, entry['answer'])
            pdf.ln(2)

            # Separator
            pdf.set_draw_color(200, 200, 200)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.set_draw_color(0, 0, 0)
            pdf.ln(3)

    # ═══════════════════════════════════════
    # PART 3: IMPROVEMENT LOG
    # ═══════════════════════════════════════
    pdf.add_page()
    pdf.set_font('malgun', 'B', 16)
    pdf.cell(0, 10, strip_emoji('Part 3: 개선 이력 (Update Log)'), new_x='LMARGIN', new_y='NEXT')
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)

    # v6.2 speed optimizations
    v62_content = """## v6.2.0 - 2026.02.12: 속도 최적화 + 라우팅 수정

### 성능 기준 정의

| 분류 | 기준 | 조치 |
|------|------|------|
| OK (정상) | < 100초 | 정상 운영 |
| WARN (위험군) | >= 100초 | 원인 분석 후 개선 필요 |
| FAIL (실패) | >= 200초 | 반드시 수정 |

### 최적화 항목 (8개)

| # | 최적화 | 파일 | 상세 |
|---|--------|------|------|
| 1 | Notion 3페이지 병렬 읽기 | notion_agent.py | asyncio.gather() 동시 3페이지 |
| 2 | Notion Google Sheets 병렬 읽기 | notion_agent.py | 최대 2개 시트 동시 조회 |
| 3 | BQ 답변 포맷팅 Flash 전환 | sql_agent.py | Pro/Opus -> Flash (15-25s -> 3-5s) |
| 4 | GWS ReAct 반복 제한 | gws_agent.py | recursion_limit=10 |
| 5 | Notion 워밍업 병렬화 | notion_agent.py | 10개 타이틀 동시 fetch |
| 6 | BQ 스키마 프리로드 | main.py | 서버 시작 시 캐시 |
| 7 | BQ 차트 생성 Flash 전환 | sql_agent.py | 차트 JSON 설정 Pro -> Flash |
| 8 | "시장" 라우팅 오분류 수정 | orchestrator.py | false multi-routing 방지 |

### 속도 개선 결과

| 도메인 | v6.1 평균 | v6.2 결과 | 개선율 |
|--------|----------|----------|--------|
| BigQuery | 50.0s | 18.5s | +63% |
| Notion | 110.3s | 36.7s | +67% |

### 느린 쿼리 최종 재테스트 (기존 100-300초 10개)

| ID | 도메인 | 원래 | v6.2 최종 | 등급 |
|----|--------|------|----------|------|
| NT-01 | Notion | 213.1s | 41.0s | OK |
| NT-04 | Notion | 269.7s | 31.1s | OK |
| R2-NT-02 | Notion | 205.6s | 32.8s | OK |
| R2-NT-03 | Notion | 174.5s | 18.6s | OK |
| R2-NT-12 | Notion | 127.6s | 45.0s | OK |
| GWS-06 | GWS | 176.8s | 9.6s | OK |
| GWS-03 | GWS | 169.9s | 23.1s | OK |
| BQ-17 | BigQuery | 171.1s | 16.5s | OK |
| R2-BQ-05 | BigQuery | 157.7s | 29.8s | OK |
| BQ-16 | BigQuery | 130.2s | 27.5s | OK |

결과: 10/10 OK, 0 WARN, 0 FAIL

### Round 4 다양성 테스트 (55개)

기존 R1-R3과 겹치지 않는 새로운 변수 테스트.
BQ 25개(미테스트 제품라인/국가/메트릭/팀), Notion 15개(세부필터/크로스페이지/모호쿼리), GWS 15개(다양한 검색패턴).

| 도메인 | OK | WARN | FAIL | 평균 |
|--------|-----|------|------|------|
| BigQuery | 25/25 | 0 | 0 | 21.3s |
| Notion | 15/15 | 0 | 0 | 28.4s |
| GWS | 15/15 | 0 | 0 | 19.0s |
| 합계 | 55/55 | 0 | 0 | 22.5s |

---

## v6.0.2 - 2026.02.12: 버그 수정 3건

- SQL CTE 구문 오류: WITH절 인식 실패 -> sanitize_sql에 CTE 추출 로직 추가
- 제품 조회 라우팅: "제품 리스트" 등 10개 키워드 추가 -> BigQuery 정상 라우팅
- 차트 y축 타입 오류: 문자열 컬럼 -> 숫자 자동 보정

---

## v6.1.0 - 2026.02.12: Notion/GWS 버그 수정 5건

### Notion Agent v6.1 (4건)

- Sheet 읽기 타임아웃: asyncio.wait_for(30s) + max_rows=50
- httpx 클라이언트 재생성: _close_client 항상 None 설정 + retry 시 재획득
- 검색어 구두점 매칭: re.sub으로 구두점 제거 후 매칭

### GWS Agent v4.1 (1건)

- ReAct 에이전트 타임아웃: asyncio.wait_for(120s) + 친절한 에러 메시지

---

## v6.0.1 - 2026.02.11: Notion API 연결 수정 + 모델 업그레이드

### Notion Retry 로직

- 공유 httpx.AsyncClient + 연결 풀링 (max_connections=5)
- _request_with_retry: 3회 재시도, 지수 백오프 (1s, 2s, 4s)
- 결과: 연결 오류 3-5/8건 -> 0/8건

### LLM 모델 업그레이드

| 역할 | 이전 | 변경 |
|------|------|------|
| 메인 응답 (Search) | Gemini 2.5 Pro | Gemini 3 Pro Preview |
| 메인 응답 (Analysis) | Claude Sonnet 4.5 | Claude Opus 4.6 |
| 경량 작업 | Gemini 2.5 Flash | Gemini 2.5 Flash (유지) |

---

## v6.0.0 - 2026.02.11: Notion Agent 허용 목록 리팩토링

- 전체 워크스페이스 크롤링(7분+) -> 허용 목록 10개 페이지만 검색 (~3초)
- UUID 포맷 변환, 타입 폴백 (database/page 자동 감지)
- LLM 폴백 검색 (키워드 실패 시 Flash가 관련 페이지 선택)
- Google Sheets URL 자동 감지 + 시트 데이터 포함

---

## v5.0.0 - 2026.02.10: Dual LLM + GWS OAuth2

### 주요 변경

- Dual LLM: Gemini (Search) + Claude (Analysis) 선택형 구조
- Flash 분리: SQL 생성/라우팅 등 경량 작업 전용
- Google Search Grounding: Gemini 네이티브 웹 검색 통합
- GWS 개별 OAuth2: MCP -> 직접 API 호출, 사용자별 인증
- Open WebUI SSO: 단일 Google 로그인으로 GWS 접근
- 속도 최적화: 38-42s -> 11-13s (키워드 분류, Flash, 병렬, 캐시)
- SKIN1004 브랜딩: 로고, 파비콘, 로그인 CSS 커스터마이징

---

## v4.0.0 - 2026.02.06: 차트 시각화 + 데이터 제한 확대

- ChatGPT 스타일 차트 디자인 (흰색 배경, 30색 팔레트)
- 데이터 레이블 직접 표시 (K/M/B 축약)
- 레전드 매출순 정렬, 동적 이미지 크기
- MAX_RESULT_ROWS: 1,000 -> 10,000행
"""

    render_md_lines(pdf, v62_content.strip().split('\n'))

    # ── Output ──
    out_path = os.path.join(base_dir, 'docs', 'comprehensive_report_2026-02-12.pdf')
    pdf.output(out_path)
    size = os.path.getsize(out_path)
    pages = pdf.page_no()
    print(f'Comprehensive report generated: {out_path}')
    print(f'  {pages} pages, {size:,} bytes, {total_entries} QA entries')

    try:
        import sys
        sys.path.insert(0, base_dir)
        from app.core.notify import notify
        notify("리포트 생성 완료", f"종합 리포트: {pages}p, {total_entries}개 QA, {size:,} bytes")
    except Exception:
        pass


if __name__ == '__main__':
    main()
