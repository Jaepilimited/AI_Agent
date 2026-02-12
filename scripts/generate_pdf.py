"""Generate polished PDF documents from markdown update logs and PRD."""

import re
from pathlib import Path

from fpdf import FPDF, XPos, YPos


class DocPDF(FPDF):
    """Custom PDF with Korean font support and clean styling."""

    # Colors
    C_BLACK = (30, 30, 30)
    C_DARK = (50, 50, 50)
    C_GRAY = (100, 100, 100)
    C_LIGHT_GRAY = (180, 180, 180)
    C_BG_GRAY = (245, 245, 245)
    C_ACCENT = (99, 102, 241)  # Indigo
    C_WHITE = (255, 255, 255)
    C_TABLE_HEADER = (55, 65, 81)
    C_TABLE_BORDER = (209, 213, 219)
    C_TABLE_ALT = (249, 250, 251)
    C_CODE_BG = (243, 244, 246)
    C_GOLD = (232, 146, 0)

    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=25)
        # Register Korean fonts
        self.add_font("MalgunGothic", "", "C:/Windows/Fonts/malgun.ttf")
        self.add_font("MalgunGothic", "B", "C:/Windows/Fonts/malgunbd.ttf")
        self.add_font("Consolas", "", "C:/Windows/Fonts/consola.ttf")

    def header(self):
        if self.page_no() > 1:
            self.set_font("MalgunGothic", "", 7)
            self.set_text_color(*self.C_LIGHT_GRAY)
            self.cell(0, 8, "SKIN1004 Enterprise AI System", align="R",
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            # Thin line
            self.set_draw_color(*self.C_LIGHT_GRAY)
            self.set_line_width(0.2)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.ln(3)

    def footer(self):
        self.set_y(-20)
        self.set_draw_color(*self.C_LIGHT_GRAY)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)
        self.set_font("MalgunGothic", "", 7)
        self.set_text_color(*self.C_LIGHT_GRAY)
        self.cell(0, 10, f"{self.page_no()}", align="C")

    def add_title_page(self, title: str, subtitle: str, version: str, date: str):
        """Add a styled title page."""
        self.add_page()
        self.ln(50)

        # Accent bar
        self.set_fill_color(*self.C_ACCENT)
        self.rect(self.l_margin, self.get_y(), 40, 3, "F")
        self.ln(12)

        # Title
        self.set_font("MalgunGothic", "B", 28)
        self.set_text_color(*self.C_BLACK)
        self.cell(0, 14, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(3)

        # Subtitle
        self.set_font("MalgunGothic", "", 14)
        self.set_text_color(*self.C_GRAY)
        self.cell(0, 10, subtitle, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(20)

        # Version / Date
        self.set_font("MalgunGothic", "", 11)
        self.set_text_color(*self.C_DARK)
        self.cell(0, 8, f"Version {version}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.cell(0, 8, date, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(5)
        self.cell(0, 8, "DB Team / Data Analytics", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def add_section_h1(self, text: str):
        """Large section header with accent bar."""
        if self.get_y() > 230:
            self.add_page()
        self.ln(8)
        self.set_fill_color(*self.C_ACCENT)
        self.rect(self.l_margin, self.get_y(), 4, 10, "F")
        self.set_font("MalgunGothic", "B", 16)
        self.set_text_color(*self.C_BLACK)
        self.set_x(self.l_margin + 8)
        self.cell(0, 10, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)
        # Bottom line
        self.set_draw_color(*self.C_TABLE_BORDER)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def add_section_h2(self, text: str):
        """Medium section header."""
        if self.get_y() > 245:
            self.add_page()
        self.ln(5)
        self.set_font("MalgunGothic", "B", 12)
        self.set_text_color(*self.C_DARK)
        self.cell(0, 8, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(220, 220, 220)
        self.set_line_width(0.15)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def add_section_h3(self, text: str):
        """Small section header."""
        if self.get_y() > 255:
            self.add_page()
        self.ln(3)
        self.set_font("MalgunGothic", "B", 10)
        self.set_text_color(70, 70, 70)
        self.cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def add_paragraph(self, text: str):
        """Normal text paragraph."""
        self.set_font("MalgunGothic", "", 9)
        self.set_text_color(*self.C_DARK)
        self.multi_cell(0, 5.5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)

    def add_bullet(self, text: str, indent: int = 0):
        """Bullet point item."""
        self.set_font("MalgunGothic", "", 9)
        self.set_text_color(*self.C_DARK)
        x = self.l_margin + 4 + indent * 6
        self.set_x(x)
        # Bullet dot
        self.set_fill_color(*self.C_ACCENT)
        self.ellipse(x, self.get_y() + 1.8, 1.8, 1.8, "F")
        self.set_x(x + 4)
        self.multi_cell(self.w - self.r_margin - x - 4, 5.5, text,
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(0.5)

    def add_table(self, headers: list, rows: list):
        """Styled table with alternating row colors."""
        if self.get_y() > 240:
            self.add_page()

        n_cols = len(headers)
        usable = self.w - self.l_margin - self.r_margin
        col_w = usable / n_cols

        # Header
        self.set_font("MalgunGothic", "B", 8)
        self.set_fill_color(*self.C_TABLE_HEADER)
        self.set_text_color(*self.C_WHITE)
        self.set_draw_color(*self.C_TABLE_BORDER)
        self.set_line_width(0.2)

        for h in headers:
            self.cell(col_w, 8, f" {h}", border=1, fill=True)
        self.ln()

        # Rows
        self.set_font("MalgunGothic", "", 8)
        self.set_text_color(*self.C_DARK)
        for i, row in enumerate(rows):
            if self.get_y() > 265:
                self.add_page()
                # Re-draw header
                self.set_font("MalgunGothic", "B", 8)
                self.set_fill_color(*self.C_TABLE_HEADER)
                self.set_text_color(*self.C_WHITE)
                for h in headers:
                    self.cell(col_w, 8, f" {h}", border=1, fill=True)
                self.ln()
                self.set_font("MalgunGothic", "", 8)
                self.set_text_color(*self.C_DARK)

            if i % 2 == 1:
                self.set_fill_color(*self.C_TABLE_ALT)
            else:
                self.set_fill_color(*self.C_WHITE)
            for cell in row:
                self.cell(col_w, 7, f" {cell}", border=1, fill=True)
            self.ln()
        self.ln(3)

    def add_code_block(self, code: str):
        """Monospace code block with background."""
        if self.get_y() > 240:
            self.add_page()
        self.set_font("MalgunGothic", "", 7.5)
        self.set_text_color(55, 65, 81)

        lines = code.strip().split("\n")
        block_h = len(lines) * 4.5 + 6
        start_y = self.get_y()

        # Background
        self.set_fill_color(*self.C_CODE_BG)
        self.rect(self.l_margin, start_y, self.w - self.l_margin - self.r_margin,
                  min(block_h, 270 - start_y), "F")

        # Left accent bar
        self.set_fill_color(*self.C_ACCENT)
        self.rect(self.l_margin, start_y, 2, min(block_h, 270 - start_y), "F")

        self.ln(3)
        for line in lines:
            if self.get_y() > 265:
                self.add_page()
            self.set_x(self.l_margin + 6)
            self.cell(0, 4.5, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(4)

    def add_separator(self):
        """Horizontal rule."""
        self.ln(4)
        self.set_draw_color(220, 220, 220)
        self.set_line_width(0.3)
        y = self.get_y()
        mid = self.w / 2
        self.line(mid - 30, y, mid + 30, y)
        self.ln(6)


def parse_md_and_build_pdf(md_path: str, pdf_path: str,
                           title: str, subtitle: str, version: str, date: str):
    """Parse markdown file and generate styled PDF."""
    content = Path(md_path).read_text(encoding="utf-8")
    lines = content.split("\n")

    pdf = DocPDF()
    pdf.add_title_page(title, subtitle, version, date)
    pdf.add_page()

    i = 0
    in_code_block = False
    code_lines = []
    in_table = False
    table_headers = []
    table_rows = []

    while i < len(lines):
        line = lines[i]

        # Code block toggle
        if line.strip().startswith("```"):
            if in_code_block:
                pdf.add_code_block("\n".join(code_lines))
                code_lines = []
                in_code_block = False
            else:
                # Flush table if active
                if in_table:
                    pdf.add_table(table_headers, table_rows)
                    in_table = False
                    table_headers = []
                    table_rows = []
                in_code_block = True
            i += 1
            continue

        if in_code_block:
            code_lines.append(line)
            i += 1
            continue

        # Table detection
        if "|" in line and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # Check if this is a separator line (---)
            if all(re.match(r'^[-:]+$', c) for c in cells):
                i += 1
                continue
            if not in_table:
                in_table = True
                table_headers = cells
            else:
                table_rows.append(cells)
            i += 1
            continue
        else:
            if in_table:
                pdf.add_table(table_headers, table_rows)
                in_table = False
                table_headers = []
                table_rows = []

        stripped = line.strip()

        # Skip empty lines
        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if stripped == "---":
            pdf.add_separator()
            i += 1
            continue

        # Headers
        if stripped.startswith("# ") and not stripped.startswith("## "):
            text = stripped[2:].strip()
            pdf.add_section_h1(text)
            i += 1
            continue

        if stripped.startswith("## "):
            text = stripped[3:].strip()
            pdf.add_section_h2(text)
            i += 1
            continue

        if stripped.startswith("### "):
            text = stripped[4:].strip()
            pdf.add_section_h3(text)
            i += 1
            continue

        if stripped.startswith("#### "):
            text = stripped[5:].strip()
            pdf.add_section_h3(text)
            i += 1
            continue

        # Bullet points
        if stripped.startswith("- "):
            text = stripped[2:]
            # Clean markdown formatting
            text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
            text = re.sub(r'`(.*?)`', r'\1', text)
            pdf.add_bullet(text)
            i += 1
            continue

        # Blockquote
        if stripped.startswith("> "):
            text = stripped[2:]
            text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
            pdf.set_font("MalgunGothic", "", 9)
            pdf.set_text_color(100, 100, 100)
            old_x = pdf.l_margin
            pdf.set_fill_color(232, 146, 0)
            pdf.rect(old_x, pdf.get_y(), 2, 12, "F")
            pdf.set_x(old_x + 6)
            pdf.multi_cell(pdf.w - pdf.r_margin - old_x - 6, 5.5, text,
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(2)
            i += 1
            continue

        # Bold title lines (like **일자:** ...)
        if stripped.startswith("**") and "**" in stripped[2:]:
            text = re.sub(r'\*\*(.*?)\*\*', r'\1', stripped)
            pdf.set_font("MalgunGothic", "B", 9)
            pdf.set_text_color(*pdf.C_DARK)
            pdf.cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(1)
            i += 1
            continue

        # Normal paragraph
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', stripped)
        text = re.sub(r'`(.*?)`', r'\1', text)
        pdf.add_paragraph(text)
        i += 1

    # Flush remaining table
    if in_table:
        pdf.add_table(table_headers, table_rows)

    pdf.output(pdf_path)
    print(f"Generated: {pdf_path}")


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent / "docs"

    # Update log
    parse_md_and_build_pdf(
        md_path=str(base / "update_log_2026-02-06.md"),
        pdf_path=str(base / "update_log_2026-02-10.pdf"),
        title="SKIN1004 AI Agent",
        subtitle="Update Log",
        version="5.0.0",
        date="2026.02.10",
    )

    # PRD
    parse_md_and_build_pdf(
        md_path=str(base / "SKIN1004_Enterprise_AI_PRD_v4.md"),
        pdf_path=str(base / "SKIN1004_Enterprise_AI_PRD_v5.pdf"),
        title="SKIN1004 Enterprise AI",
        subtitle="Product Requirements Document",
        version="5.0.0",
        date="2026.02.10",
    )
