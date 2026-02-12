"""
Generate comprehensive QA detail PDF from all test result files.
Shows every question asked and the full answer received.
"""
import re
import os
import sys
from fpdf import FPDF


class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('malgun', '', 8)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', align='C')


def strip_emoji(text: str) -> str:
    """Remove emoji and special unicode symbols."""
    # Remove common emoji ranges
    emoji_pattern = re.compile(
        '['
        '\U0001F300-\U0001F9FF'  # Misc Symbols, Emoticons, etc.
        '\U00002702-\U000027B0'  # Dingbats
        '\U0000FE0F'             # Variation selector
        '\U00002139'             # Info
        '\U000026A0'             # Warning
        '\U00002705'             # Check mark
        '\U00002611'             # Ballot box
        '\U0001F5D3'             # Spiral calendar
        '\u30FC'                 # Katakana dash
        ']+', re.UNICODE
    )
    return emoji_pattern.sub('', text)


def clean(text: str) -> str:
    """Strip markdown formatting and emoji for plain text output."""
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = strip_emoji(text)
    return text.strip()


def parse_result_file(filepath: str) -> list[dict]:
    """Parse a test result .txt file and extract individual test entries."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    entries = []
    # Split by the separator line
    blocks = re.split(r'={50,}', content)

    i = 0
    while i < len(blocks):
        block = blocks[i].strip()
        # Look for test ID pattern like [BQ-01], [NT-03], [E-01], [REG-01], etc.
        id_match = re.match(r'^\[([A-Z0-9\-]+)\](.*)$', block, re.MULTILINE)
        if id_match:
            test_id = id_match.group(1)
            category_line = id_match.group(2).strip()

            # Extract Q, Expected, Status from the block
            q_match = re.search(r'^Q:\s*(.+)$', block, re.MULTILINE)
            expected_match = re.search(r'^Expected:\s*(.+)$', block, re.MULTILINE)
            status_match = re.search(r'^Status:\s*(.+)$', block, re.MULTILINE)
            fix_match = re.search(r'^Fix:\s*(.+)$', block, re.MULTILINE)

            question = q_match.group(1).strip() if q_match else ''
            expected = expected_match.group(1).strip() if expected_match else ''
            status_line = status_match.group(1).strip() if status_match else ''
            fix_desc = fix_match.group(1).strip() if fix_match else ''

            # Parse status details
            time_match = re.search(r'([\d.]+)s', status_line)
            chars_match = re.search(r'(\d+)ch', status_line)
            status_val = 'OK' if 'OK' in status_line else ('EXCEPTION' if 'EXCEPTION' in status_line else 'OTHER')

            # The answer is after the separator line (______ or ──────)
            answer = ''
            underline_pos = block.find('_' * 20)
            if underline_pos < 0:
                underline_pos = block.find('\u2500' * 20)  # ─ (box drawing)
            if underline_pos >= 0:
                answer = block[underline_pos:].strip()
                # Remove the separator line itself (underscores or box drawing chars)
                answer = re.sub(r'^[_\u2500]+\s*', '', answer).strip()

            entries.append({
                'id': test_id,
                'category': category_line,
                'question': question,
                'expected': expected,
                'status': status_val,
                'status_line': status_line,
                'time': float(time_match.group(1)) if time_match else 0,
                'chars': int(chars_match.group(1)) if chars_match else 0,
                'fix': fix_desc,
                'answer': answer,
            })
        elif block.startswith('SUMMARY'):
            break
        i += 1

    return entries


def render_answer(pdf: FPDF, answer: str):
    """Render markdown-formatted answer text to PDF."""
    if not answer:
        pdf.set_font('malgun', '', 9)
        pdf.set_text_color(150, 150, 150)
        pdf.multi_cell(0, 5, '(No answer)')
        pdf.set_text_color(0, 0, 0)
        return

    in_table = False
    table_rows = []

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
        max_cell_chars = int(col_w / 2.2)
        if max_cell_chars < 8:
            max_cell_chars = 8
        for ri, row in enumerate(table_rows):
            if ri == 0:
                pdf.set_font('malgun', 'B', 7)
                pdf.set_fill_color(230, 235, 245)
                for cell in row:
                    pdf.cell(col_w, 5, clean(cell)[:max_cell_chars], border=1, fill=True)
                pdf.ln()
            else:
                pdf.set_font('malgun', '', 7)
                for cell in row:
                    pdf.cell(col_w, 4.5, clean(cell)[:max_cell_chars], border=1)
                pdf.ln()
        table_rows = []
        in_table = False
        pdf.ln(1)

    lines = answer.split('\n')
    for line in lines:
        line = line.rstrip()

        # Table
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
        if stripped.startswith('### '):
            pdf.set_font('malgun', 'B', 9)
            pdf.multi_cell(0, 5, clean(stripped[4:]))
            pdf.ln(1)
        elif stripped.startswith('## '):
            pdf.set_font('malgun', 'B', 10)
            pdf.multi_cell(0, 5, clean(stripped[3:]))
            pdf.ln(1)
        elif stripped.startswith('#### '):
            pdf.set_font('malgun', 'B', 8)
            pdf.multi_cell(0, 5, clean(stripped[5:]))
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
            indent = len(stripped) - len(stripped.lstrip())
            indent_mm = min(indent * 1.5, 20)
            pdf.set_font('malgun', '', 8)
            pdf.set_x(pdf.l_margin + indent_mm)
            pdf.multi_cell(0, 4, clean(stripped.strip()))
        elif stripped.startswith('---'):
            pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
            pdf.ln(3)
        else:
            pdf.set_font('malgun', '', 8)
            pdf.multi_cell(0, 4, clean(stripped))

    if in_table:
        flush_table()


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Define all test result files in order
    test_files = [
        {
            'file': 'test_team_bigquery_result.txt',
            'round': 'Round 1',
            'domain': 'BigQuery (Sales Data)',
            'desc': 'BigQuery SQL Agent - 20 queries covering sales trends, comparisons, product analysis',
        },
        {
            'file': 'test_team_notion_result.txt',
            'round': 'Round 1',
            'domain': 'Notion (Documents)',
            'desc': 'Notion Agent - 20 queries covering document search, procedures, guides',
        },
        {
            'file': 'test_team_gws_result.txt',
            'round': 'Round 1',
            'domain': 'Google Workspace',
            'desc': 'GWS Agent - 15 queries covering Gmail, Drive, Calendar',
        },
        {
            'file': 'test_team_r2_bigquery_result.txt',
            'round': 'Round 2',
            'domain': 'BigQuery (Sales Data)',
            'desc': 'BigQuery SQL Agent - 15 advanced queries: brand analysis, market penetration, margins',
        },
        {
            'file': 'test_team_r2_notion_result.txt',
            'round': 'Round 2',
            'domain': 'Notion (Documents)',
            'desc': 'Notion Agent - 12 queries: sheet data, cross-page analysis, complex lookups',
        },
        {
            'file': 'test_team_r2_gws_result.txt',
            'round': 'Round 2',
            'domain': 'Google Workspace',
            'desc': 'GWS Agent - 10 queries: login alerts, comprehensive search, daily briefing',
        },
        {
            'file': 'test_team_r3_edge_result.txt',
            'round': 'Round 3',
            'domain': 'Edge Cases',
            'desc': 'Edge cases - 15 queries: ambiguous routing, wrong premises, mixed language, complex analysis',
        },
        {
            'file': 'test_regression_result.txt',
            'round': 'Regression',
            'domain': 'Bug Fix Verification',
            'desc': 'Regression tests - 5 queries verifying bug fixes (httpx, timeout, punctuation, GWS timeout)',
        },
    ]

    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_font('malgun', '', 'C:/Windows/Fonts/malgun.ttf')
    pdf.add_font('malgun', 'B', 'C:/Windows/Fonts/malgunbd.ttf')
    pdf.set_auto_page_break(auto=True, margin=15)

    # ── Cover page ──
    pdf.add_page()
    pdf.ln(40)
    pdf.set_font('malgun', 'B', 24)
    pdf.cell(0, 15, 'SKIN1004 AI Agent', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('malgun', 'B', 20)
    pdf.cell(0, 12, 'QA Test Detail Report', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(10)
    pdf.set_font('malgun', '', 12)
    pdf.cell(0, 8, '107 Queries across 3 Rounds + Regression', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 8, 'Date: 2026-02-12', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(20)

    # Summary table on cover
    pdf.set_font('malgun', 'B', 11)
    pdf.cell(0, 8, 'Test Summary', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(3)

    summary_data = [
        ['Round', 'Domain', 'Queries', 'OK', 'Issues'],
        ['Round 1', 'BigQuery', '20', '19', '1 SHORT'],
        ['Round 1', 'Notion', '20', '18', '1 EXCEPTION, 1 MISS'],
        ['Round 1', 'GWS', '15', '13', '1 EXCEPTION, 1 EMPTY'],
        ['Round 2', 'BigQuery', '15', '15', '-'],
        ['Round 2', 'Notion', '12', '10', '1 EXCEPTION, 1 MISS'],
        ['Round 2', 'GWS', '10', '8', '1 EXCEPTION, 1 EMPTY'],
        ['Round 3', 'Edge Cases', '15', '15', '-'],
        ['Regression', 'Bug Fixes', '5', '5', '-'],
        ['TOTAL', '', '112', '103', '9 issues (all fixed)'],
    ]
    col_widths = [30, 30, 20, 15, 75]
    for ri, row in enumerate(summary_data):
        if ri == 0 or ri == len(summary_data) - 1:
            pdf.set_font('malgun', 'B', 9)
            pdf.set_fill_color(230, 235, 245)
        else:
            pdf.set_font('malgun', '', 9)
            pdf.set_fill_color(255, 255, 255)
        for ci, cell in enumerate(row):
            pdf.cell(col_widths[ci], 6, cell, border=1, fill=(ri == 0 or ri == len(summary_data) - 1))
        pdf.ln()

    pdf.ln(10)
    pdf.set_font('malgun', '', 9)
    pdf.multi_cell(0, 5, clean(
        'This document contains the full question and answer for every test query '
        'executed during QA testing of the SKIN1004 AI Agent system. '
        'Each entry shows the test ID, question asked, status/timing, and the complete response.'
    ))

    # ── Process each test file ──
    total_count = 0
    for tf in test_files:
        filepath = os.path.join(base_dir, tf['file'])
        if not os.path.exists(filepath):
            print(f"  SKIP: {tf['file']} not found")
            continue

        entries = parse_result_file(filepath)
        if not entries:
            print(f"  SKIP: {tf['file']} has no entries")
            continue

        total_count += len(entries)
        print(f"  {tf['file']}: {len(entries)} entries")

        # Section header (new page)
        pdf.add_page()
        pdf.set_font('malgun', 'B', 16)
        pdf.set_fill_color(40, 60, 120)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 12, f"  {tf['round']} - {tf['domain']}", fill=True, new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

        pdf.set_font('malgun', '', 9)
        pdf.multi_cell(0, 5, tf['desc'])
        pdf.ln(3)

        # Section summary
        ok_count = sum(1 for e in entries if e['status'] == 'OK')
        avg_time = sum(e['time'] for e in entries) / len(entries) if entries else 0
        pdf.set_font('malgun', 'B', 9)
        pdf.cell(0, 6, f"Results: {ok_count}/{len(entries)} OK  |  Avg: {avg_time:.1f}s", new_x='LMARGIN', new_y='NEXT')
        pdf.ln(2)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(5)

        # Each entry
        for entry in entries:
            # Check if we need a new page (at least 60mm space needed)
            if pdf.get_y() > pdf.h - 65:
                pdf.add_page()

            # Entry header bar
            status_color = (34, 139, 34) if entry['status'] == 'OK' else (220, 50, 50)
            pdf.set_fill_color(*status_color)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font('malgun', 'B', 10)

            header_text = f"  [{entry['id']}]  {entry['category']}"
            if entry['fix']:
                header_text += f"  |  Fix: {entry['fix']}"
            pdf.cell(0, 8, header_text, fill=True, new_x='LMARGIN', new_y='NEXT')
            pdf.set_text_color(0, 0, 0)

            # Question
            pdf.set_fill_color(245, 247, 250)
            pdf.set_font('malgun', 'B', 9)
            pdf.cell(0, 6, f"  Q: {entry['question']}", fill=True, new_x='LMARGIN', new_y='NEXT')

            # Status line
            pdf.set_font('malgun', '', 8)
            pdf.set_text_color(100, 100, 100)
            status_info = f"  Status: {entry['status_line']}"
            if entry['expected']:
                status_info += f"  |  Expected: {entry['expected']}"
            pdf.cell(0, 5, status_info, new_x='LMARGIN', new_y='NEXT')
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2)

            # Answer
            render_answer(pdf, entry['answer'])
            pdf.ln(5)

            # Separator
            pdf.set_draw_color(200, 200, 200)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.set_draw_color(0, 0, 0)
            pdf.ln(5)

    # ── Final page ──
    pdf.add_page()
    pdf.ln(30)
    pdf.set_font('malgun', 'B', 16)
    pdf.cell(0, 12, 'End of Report', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(5)
    pdf.set_font('malgun', '', 10)
    pdf.cell(0, 8, f'Total queries documented: {total_count}', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 8, 'SKIN1004 Enterprise AI Agent v6.1', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 8, 'Generated: 2026-02-12', align='C', new_x='LMARGIN', new_y='NEXT')

    out_path = os.path.join(base_dir, 'docs', 'qa_detail_report_2026-02-12.pdf')
    pdf.output(out_path)
    size = os.path.getsize(out_path)
    print(f"\nPDF generated: {out_path}")
    print(f"Size: {size:,} bytes")
    print(f"Total entries: {total_count}")


if __name__ == '__main__':
    main()
