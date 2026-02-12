"""Generate Round 4 Diverse QA Test Report PDF.

Reads test_round4_diverse_result.txt and produces a comprehensive PDF report.
"""
import re
import os
from datetime import datetime
from fpdf import FPDF


class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('malgun', '', 8)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', align='C')


EMOJI_RE = re.compile(
    '[\U0001F300-\U0001F9FF\U00002702-\U000027B0'
    '\U0000FE0F\U00002139\U000026A0\U00002705\U00002611'
    '\U0001F5D3\u30FC]+', re.UNICODE
)


def strip_emoji(t):
    return EMOJI_RE.sub('', t)


def clean(t):
    t = re.sub(r'\*\*(.*?)\*\*', r'\1', t)
    t = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', t)
    t = re.sub(r'`([^`]+)`', r'\1', t)
    return strip_emoji(t).strip()


def render_md_lines(pdf, lines):
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
        if col_w < 18:
            col_w = 18
        max_chars = max(int(col_w / 2.2), 6)
        fs = 7 if col_count <= 5 else 6
        for ri, row in enumerate(table_rows):
            while len(row) < col_count:
                row.append('')
            if ri == 0:
                pdf.set_font('malgun', 'B', fs)
                pdf.set_fill_color(230, 235, 245)
                for cell in row[:col_count]:
                    pdf.cell(col_w, 5, clean(cell)[:max_chars], border=1, fill=True)
                pdf.ln()
            else:
                pdf.set_font('malgun', '', fs)
                for cell in row[:col_count]:
                    pdf.cell(col_w, 4.5, clean(cell)[:max_chars], border=1)
                pdf.ln()
        table_rows = []
        in_table = False
        pdf.ln(1)

    for line in lines:
        line = line.rstrip()
        if line.strip().startswith('```'):
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
        stripped = strip_emoji(line)
        try:
            if stripped.startswith('### '):
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
                indent_mm = min(indent * 1.5, 15)
                pdf.set_font('malgun', '', 8)
                text = clean(stripped.lstrip().lstrip('-* '))
                x_pos = pdf.l_margin + indent_mm
                if x_pos > pdf.w - pdf.r_margin - 30:
                    x_pos = pdf.l_margin + 5
                pdf.set_x(x_pos)
                pdf.multi_cell(0, 4, '  - ' + text)
            elif stripped.startswith('---'):
                pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
                pdf.ln(3)
            else:
                pdf.set_font('malgun', '', 8)
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(0, 4, clean(stripped))
        except Exception:
            pdf.set_x(pdf.l_margin)
            pdf.set_font('malgun', '', 7)
            pdf.cell(0, 4, clean(stripped)[:120], new_x='LMARGIN', new_y='NEXT')

    if in_table:
        flush_table()


def parse_result_file(filepath):
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

        question = q_match.group(1).strip() if q_match else ''
        expected = expected_match.group(1).strip() if expected_match else ''
        status_line = status_match.group(1).strip() if status_match else ''

        time_match = re.search(r'([\d.]+)s', status_line)
        status_val = 'OK' if 'OK' in status_line else ('WARN' if 'WARN' in status_line else ('FAIL' if 'FAIL' in status_line else 'OTHER'))

        answer = ''
        underline_pos = block.find('_' * 20)
        if underline_pos >= 0:
            answer = block[underline_pos:].strip()
            answer = re.sub(r'^[_]+\s*', '', answer).strip()

        entries.append({
            'id': test_id,
            'category': category_line,
            'question': question,
            'expected': expected,
            'status': status_val,
            'status_line': status_line,
            'time': float(time_match.group(1)) if time_match else 0,
            'answer': answer,
        })

    return entries


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result_file = os.path.join(base_dir, 'test_round4_diverse_result.txt')

    entries = parse_result_file(result_file)
    if not entries:
        print("No entries found!")
        return

    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_font('malgun', '', 'C:/Windows/Fonts/malgun.ttf')
    pdf.add_font('malgun', 'B', 'C:/Windows/Fonts/malgunbd.ttf')
    pdf.set_auto_page_break(auto=True, margin=15)

    # --- Cover Page ---
    pdf.add_page()
    pdf.ln(30)
    pdf.set_font('malgun', 'B', 24)
    pdf.cell(0, 15, 'SKIN1004 AI Agent', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(5)
    pdf.set_font('malgun', 'B', 18)
    pdf.cell(0, 12, 'Round 4 Diverse QA Test Report', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(5)
    pdf.set_font('malgun', '', 11)
    pdf.cell(0, 8, strip_emoji('새로운 변수 기반 다양성 테스트 (55 queries)'), align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(8)
    pdf.set_font('malgun', '', 10)
    pdf.cell(0, 7, f'Date: {datetime.now().strftime("%Y-%m-%d")}', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 7, 'Version: v6.2.0', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 7, 'DB Team / Data Analytics', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(15)

    # Stats
    total = len(entries)
    ok_count = sum(1 for e in entries if e['status'] == 'OK')
    warn_count = sum(1 for e in entries if e['status'] == 'WARN')
    fail_count = sum(1 for e in entries if e['status'] == 'FAIL')
    avg_time = sum(e['time'] for e in entries) / total if total > 0 else 0

    bq_entries = [e for e in entries if 'BQ' in e['id']]
    nt_entries = [e for e in entries if 'NT' in e['id']]
    gws_entries = [e for e in entries if 'GWS' in e['id']]

    bq_avg = sum(e['time'] for e in bq_entries) / len(bq_entries) if bq_entries else 0
    nt_avg = sum(e['time'] for e in nt_entries) / len(nt_entries) if nt_entries else 0
    gws_avg = sum(e['time'] for e in gws_entries) / len(gws_entries) if gws_entries else 0

    # Key metrics table
    pdf.set_font('malgun', 'B', 11)
    pdf.cell(0, 8, strip_emoji('핵심 지표'), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(2)
    metrics = [
        ['항목', '값'],
        ['총 테스트', f'{total}개 (BQ {len(bq_entries)} + Notion {len(nt_entries)} + GWS {len(gws_entries)})'],
        ['성공률', f'{(ok_count / total) * 100:.1f}% ({ok_count}/{total} OK)'],
        ['WARN/FAIL', f'WARN {warn_count}건, FAIL {fail_count}건'],
        ['전체 평균 응답', f'{avg_time:.1f}s'],
        ['BigQuery 평균', f'{bq_avg:.1f}s'],
        ['Notion 평균', f'{nt_avg:.1f}s'],
        ['GWS 평균', f'{gws_avg:.1f}s'],
    ]
    col_widths = [50, 120]
    for ri, row in enumerate(metrics):
        if ri == 0:
            pdf.set_font('malgun', 'B', 9)
            pdf.set_fill_color(230, 235, 245)
        else:
            pdf.set_font('malgun', '', 9)
        for ci, cell in enumerate(row):
            pdf.cell(col_widths[ci], 6, clean(cell), border=1, fill=(ri == 0))
        pdf.ln()

    # --- Detailed Results ---
    domain_groups = [
        ('BigQuery', bq_entries),
        ('Notion', nt_entries),
        ('GWS', gws_entries),
    ]

    for domain_name, domain_entries in domain_groups:
        pdf.add_page()
        pdf.set_font('malgun', 'B', 16)
        pdf.cell(0, 10, f'{domain_name} Tests ({len(domain_entries)})', new_x='LMARGIN', new_y='NEXT')
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(5)

        for entry in domain_entries:
            # Header
            status_color = (0, 128, 0) if entry['status'] == 'OK' else (200, 0, 0) if entry['status'] == 'FAIL' else (200, 150, 0)
            pdf.set_font('malgun', 'B', 9)
            pdf.set_fill_color(240, 242, 248)
            header = f"[{entry['id']}] {entry['question'][:80]}"
            pdf.cell(0, 6, clean(header), fill=True, new_x='LMARGIN', new_y='NEXT')

            # Status
            pdf.set_font('malgun', '', 8)
            pdf.set_text_color(*status_color)
            pdf.cell(0, 5, f"Status: {strip_emoji(entry['status_line'][:100])}", new_x='LMARGIN', new_y='NEXT')
            pdf.set_text_color(0, 0, 0)

            # Answer
            pdf.ln(1)
            if entry['answer']:
                answer_lines = entry['answer'].split('\n')
                # Limit to first 30 lines to prevent huge entries
                render_md_lines(pdf, answer_lines[:30])
                if len(answer_lines) > 30:
                    pdf.set_font('malgun', '', 7)
                    pdf.set_text_color(100, 100, 100)
                    pdf.cell(0, 4, f'... ({len(answer_lines) - 30} more lines)', new_x='LMARGIN', new_y='NEXT')
                    pdf.set_text_color(0, 0, 0)
            pdf.ln(2)

            # Separator
            pdf.set_draw_color(200, 200, 200)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.set_draw_color(0, 0, 0)
            pdf.ln(3)

    # --- Summary Table ---
    pdf.add_page()
    pdf.set_font('malgun', 'B', 14)
    pdf.cell(0, 10, strip_emoji('전체 결과 요약'), new_x='LMARGIN', new_y='NEXT')
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)

    # Results table
    col_ws = [22, 55, 15, 15, 70]
    headers = ['ID', 'Query', 'Time', 'Grade', 'Variable']
    pdf.set_font('malgun', 'B', 7)
    pdf.set_fill_color(50, 60, 80)
    pdf.set_text_color(255, 255, 255)
    for ci, h in enumerate(headers):
        pdf.cell(col_ws[ci], 5, h, border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)

    for r in entries:
        pdf.set_font('malgun', '', 6)
        pdf.cell(col_ws[0], 4, r['id'], border=1)
        pdf.cell(col_ws[1], 4, clean(r['question'])[:35], border=1)
        pdf.cell(col_ws[2], 4, f"{r['time']:.1f}s", border=1)
        if r['status'] == 'OK':
            pdf.set_text_color(0, 128, 0)
        elif r['status'] == 'WARN':
            pdf.set_text_color(200, 150, 0)
        else:
            pdf.set_text_color(200, 0, 0)
        pdf.cell(col_ws[3], 4, r['status'], border=1)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(col_ws[4], 4, clean(r['category'])[:40], border=1)
        pdf.ln()

    # Output
    out_path = os.path.join(base_dir, 'docs', 'test_report_round4_2026-02-12.pdf')
    pdf.output(out_path)
    size = os.path.getsize(out_path)
    pages = pdf.page_no()
    print(f'Report generated: {out_path}')
    print(f'  {pages} pages, {size:,} bytes, {total} entries')

    try:
        import sys
        sys.path.insert(0, base_dir)
        from app.core.notify import notify
        notify("리포트 생성 완료", f"R4 리포트: {pages}p, {total}개 QA, {size:,} bytes")
    except Exception:
        pass


if __name__ == '__main__':
    main()
