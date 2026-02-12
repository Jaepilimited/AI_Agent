"""Generate PDF from test report markdown."""
from fpdf import FPDF
import re
import os


class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('malgun', '', 8)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', align='C')


def clean(text):
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    return text.strip()


def main():
    import sys
    md_file = sys.argv[1] if len(sys.argv) > 1 else 'docs/test_report_2026-02-11.md'
    pdf_file = md_file.replace('.md', '.pdf')
    with open(md_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_font('malgun', '', 'C:/Windows/Fonts/malgun.ttf')
    pdf.add_font('malgun', 'B', 'C:/Windows/Fonts/malgunbd.ttf')
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

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
        if col_w < 15:
            col_w = 15
        for i, row in enumerate(table_rows):
            if i == 0:
                pdf.set_font('malgun', 'B', 8)
                pdf.set_fill_color(240, 240, 240)
                for cell in row:
                    pdf.cell(col_w, 6, clean(cell)[:40], border=1, fill=True)
                pdf.ln()
            else:
                pdf.set_font('malgun', '', 8)
                for cell in row:
                    pdf.cell(col_w, 5, clean(cell)[:40], border=1)
                pdf.ln()
        table_rows = []
        in_table = False
        pdf.ln(2)

    for idx, line in enumerate(lines):
        line = line.rstrip()

        # Skip code fences
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue

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
            pdf.ln(3)
            continue

        # Always reset X position to left margin
        pdf.set_x(pdf.l_margin)

        if line.startswith('# ') and not line.startswith('## '):
            pdf.set_font('malgun', 'B', 16)
            pdf.cell(0, 10, clean(line[2:]), new_x='LMARGIN', new_y='NEXT')
            pdf.ln(3)
        elif line.startswith('## '):
            pdf.ln(5)
            pdf.set_font('malgun', 'B', 13)
            pdf.cell(0, 8, clean(line[3:]), new_x='LMARGIN', new_y='NEXT')
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(3)
        elif line.startswith('### '):
            pdf.ln(3)
            pdf.set_font('malgun', 'B', 11)
            pdf.cell(0, 7, clean(line[4:]), new_x='LMARGIN', new_y='NEXT')
            pdf.ln(1)
        elif line.startswith('> '):
            pdf.set_font('malgun', '', 9)
            pdf.set_text_color(80, 80, 80)
            pdf.multi_cell(0, 5, clean(line[2:]))
            pdf.set_text_color(0, 0, 0)
            pdf.ln(1)
        elif line.lstrip().startswith('- '):
            pdf.set_font('malgun', '', 9)
            text = clean(line.lstrip().lstrip('- '))
            pdf.multi_cell(0, 5, '  - ' + text)
        elif re.match(r'^\s*\d+\.', line):
            pdf.set_font('malgun', '', 9)
            pdf.multi_cell(0, 5, clean(line))
        elif line.startswith('---'):
            pdf.line(pdf.l_margin, pdf.get_y() + 2, pdf.w - pdf.r_margin, pdf.get_y() + 2)
            pdf.ln(5)
        else:
            pdf.set_font('malgun', '', 9)
            pdf.multi_cell(0, 5, clean(line))

    if in_table:
        flush_table()

    pdf.output(pdf_file)
    size = os.path.getsize(pdf_file)
    print(f'PDF generated: {size:,} bytes')


if __name__ == '__main__':
    main()
