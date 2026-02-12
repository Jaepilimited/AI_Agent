"""Response post-processor for consistent markdown formatting.

Ensures all agent responses render well in Open WebUI.
"""

import re

import structlog

logger = structlog.get_logger(__name__)


def ensure_formatting(answer: str, domain: str = "") -> str:
    """Post-process LLM answer for consistent markdown rendering.

    Args:
        answer: Raw LLM-generated answer text.
        domain: Route domain (bigquery, notion, gws, multi, direct).

    Returns:
        Cleaned and normalized markdown text.
    """
    if not answer or not answer.strip():
        return answer

    text = answer

    # 1. Ensure blank line before headings (Open WebUI needs this)
    text = re.sub(r'([^\n])\n(#{1,4} )', r'\1\n\n\2', text)

    # 2. Ensure blank line after headings
    text = re.sub(r'(#{1,4} [^\n]+)\n([^\n#>|\-\s])', r'\1\n\n\2', text)

    # 3. Ensure table separator row exists (fix malformed tables)
    lines = text.split('\n')
    fixed_lines = []
    i = 0
    while i < len(lines):
        fixed_lines.append(lines[i])
        # If this line looks like a table header (has |) and next line is data (not separator)
        if (
            '|' in lines[i]
            and lines[i].strip().startswith('|')
            and i + 1 < len(lines)
        ):
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            # Check if next line is NOT a separator (---|)
            if next_line and '|' in next_line and not re.match(r'^[\s|:\-]+$', next_line):
                # Check if previous line was already a separator
                if not (fixed_lines and re.match(r'^[\s|:\-]+$', fixed_lines[-1].strip())):
                    # This might be the header row — check if there's no separator yet
                    # Only insert if we haven't already seen a separator
                    cols = lines[i].count('|') - 1
                    if cols > 0:
                        separator = '| ' + ' | '.join(['---'] * cols) + ' |'
                        # Only insert separator if the NEXT line after i is data, not separator
                        next_stripped = lines[i + 1].strip() if i + 1 < len(lines) else ""
                        if next_stripped.startswith('|') and '---' not in next_stripped:
                            # Check if separator already exists between current and next
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

    return text.strip()
