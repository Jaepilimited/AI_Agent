"""Response post-processor for consistent markdown formatting.

Ensures all agent responses render well in the custom frontend.
Enterprise-grade output quality: consistent spacing, visual hierarchy, source attribution.
v2.0: Follow-up normalization + auto source footer.
"""

import re

import structlog

logger = structlog.get_logger(__name__)

# Source labels per domain
_SOURCE_LABELS = {
    "bigquery": "BigQuery 매출 데이터",
    "notion": "Notion 사내 문서",
    "gws": "Google Workspace",
    "cs": "CS 제품 Q&A 데이터베이스",
    "multi": "BigQuery + Google 검색",
    "direct": "",
    "bigquery_fallback": "BigQuery (대체 응답)",
}


def ensure_formatting(answer: str, domain: str = "") -> str:
    """Post-process LLM answer for consistent markdown rendering.

    Args:
        answer: Raw LLM-generated answer text.
        domain: Route domain (bigquery, notion, gws, cs, multi, direct).

    Returns:
        Cleaned and normalized markdown text.
    """
    if not answer or not answer.strip():
        return answer

    text = answer

    # 1. Ensure blank line before headings (frontend needs this)
    text = re.sub(r'([^\n])\n(#{1,4} )', r'\1\n\n\2', text)

    # 2. Ensure blank line after headings
    text = re.sub(r'(#{1,4} [^\n]+)\n([^\n#>|\-\s])', r'\1\n\n\2', text)

    # 3. Ensure table separator row exists (fix malformed tables)
    lines = text.split('\n')
    fixed_lines = []
    i = 0
    while i < len(lines):
        fixed_lines.append(lines[i])
        if (
            '|' in lines[i]
            and lines[i].strip().startswith('|')
            and i + 1 < len(lines)
        ):
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if next_line and '|' in next_line and not re.match(r'^[\s|:\-]+$', next_line):
                if not (fixed_lines and re.match(r'^[\s|:\-]+$', fixed_lines[-1].strip())):
                    cols = lines[i].count('|') - 1
                    if cols > 0:
                        next_stripped = lines[i + 1].strip() if i + 1 < len(lines) else ""
                        if next_stripped.startswith('|') and '---' not in next_stripped:
                            pass  # Don't auto-insert — too risky for false positives
        i += 1
    text = '\n'.join(fixed_lines)

    # 4. Ensure blank line before blockquotes for proper rendering
    text = re.sub(r'([^\n])\n(> )', r'\1\n\n\2', text)

    # 5. Ensure blank line before horizontal rules
    text = re.sub(r'([^\n])\n(---)', r'\1\n\n\2', text)

    # 6. Clean up excessive blank lines (max 2 consecutive)
    text = re.sub(r'\n{4,}', '\n\n\n', text)

    # 7. Strip trailing whitespace on each line
    text = '\n'.join(line.rstrip() for line in text.split('\n'))

    # 8. Ensure consistent blockquote formatting for follow-up suggestions
    text = _normalize_followup_block(text)

    # 9. Add source attribution footer if missing (domain-specific)
    text = _ensure_source_footer(text, domain)

    return text.strip()


def _normalize_followup_block(text: str) -> str:
    """Ensure follow-up suggestion blocks are properly formatted.

    Normalizes different LLM variations into a single continuous blockquote.
    Fixes the common issue where LLMs insert blank lines between > items,
    causing each line to render as a separate blockquote.

    Input (broken):
        > 💡 **이런 것도 물어보세요**
        (blank)
        > - question 1
        (blank)
        > - question 2

    Output (fixed):
        > 💡 **이런 것도 물어보세요**
        > - question 1
        > - question 2
    """
    lines = text.split('\n')
    result = []
    in_followup = False
    for line in lines:
        stripped = line.strip()
        # Detect follow-up block start
        if '💡' in stripped and ('물어보세요' in stripped or '질문' in stripped):
            in_followup = True
            if not stripped.startswith('>'):
                result.append(f'> {stripped}')
            else:
                result.append(line)
            continue
        # Inside follow-up block
        if in_followup:
            # Suggestion line with or without blockquote prefix
            clean = stripped.lstrip('> ').strip()
            if clean.startswith('- ') or clean.startswith('* '):
                result.append(f'> {clean}')
                continue
            # Skip blank lines between follow-up items (collapse into single block)
            if stripped == '' or stripped == '>':
                continue
            # Non-suggestion, non-empty line → end of follow-up block
            in_followup = False
        result.append(line)
    return '\n'.join(result)


def _ensure_source_footer(text: str, domain: str) -> str:
    """Add source attribution footer if not already present.

    For direct/knowledge answers (5+ lines, no existing footer),
    adds a date-stamped attribution line.
    """
    from datetime import datetime

    # Skip if already has a footer (조회 기준, 출처, AI 생성, 분석 기준)
    if '조회 기준' in text or '출처:' in text or 'AI 생성' in text or '분석 기준' in text:
        return text
    # Skip short answers (greetings, single-line)
    if text.count('\n') < 5:
        return text
    # Only add footer for direct and multi routes (others have built-in footers)
    if domain and domain not in ('direct', 'multi', ''):
        return text

    today = datetime.now().strftime("%Y-%m-%d")

    # Insert footer before the follow-up block (if present), or at the end
    lines = text.split('\n')
    followup_line_idx = -1
    for i, line in enumerate(lines):
        if '💡' in line:
            followup_line_idx = i
            break

    if domain == 'multi':
        footer = f"*분석 기준: SKIN1004 내부 데이터 + Google 검색 · {today}*"
    else:
        footer = f"*AI 생성 답변 · {today}*"

    if followup_line_idx != -1:
        # Find the actual content end (skip blank lines before follow-up)
        insert_idx = followup_line_idx
        while insert_idx > 0 and lines[insert_idx - 1].strip() == '':
            insert_idx -= 1
        before_lines = lines[:insert_idx]
        after_lines = lines[followup_line_idx:]
        # Ensure --- separator exists
        before_text = '\n'.join(before_lines).rstrip()
        if not before_text.endswith('---'):
            before_text += '\n\n---'
        return f"{before_text}\n{footer}\n\n" + '\n'.join(after_lines)
    else:
        # No follow-up block — add at the end
        text = text.rstrip()
        if not text.endswith('---'):
            text += '\n\n---'
        return f"{text}\n{footer}"
