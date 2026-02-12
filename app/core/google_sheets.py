"""Google Sheets API wrapper using service account credentials.

Reads spreadsheet data and converts it to a markdown table
for inclusion in LLM context.
"""

import re

import structlog
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from app.config import get_settings

logger = structlog.get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def read_google_sheet(
    spreadsheet_id: str,
    range: str = "A:Z",
    max_rows: int = 100,
) -> str:
    """Read data from a Google Sheet and return as markdown table.

    Args:
        spreadsheet_id: The ID from the Google Sheets URL.
        range: Cell range to read (default: all columns).
        max_rows: Maximum number of rows to return (default: 100).

    Returns:
        Markdown-formatted table string, or empty string on error.
    """
    settings = get_settings()
    creds_path = settings.google_application_credentials

    try:
        creds = Credentials.from_service_account_file(creds_path, scopes=_SCOPES)
        service = build("sheets", "v4", credentials=creds)

        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range)
            .execute()
        )
        rows = result.get("values", [])

        if not rows:
            logger.info("google_sheet_empty", spreadsheet_id=spreadsheet_id)
            return ""

        # Limit rows (header + data)
        if len(rows) > max_rows + 1:
            rows = rows[: max_rows + 1]

        return _rows_to_markdown(rows)

    except Exception as e:
        logger.error(
            "google_sheet_read_failed",
            spreadsheet_id=spreadsheet_id,
            error=repr(e),
        )
        return ""


def _rows_to_markdown(rows: list[list[str]]) -> str:
    """Convert a list of rows to a markdown table.

    First row is treated as the header.
    """
    if not rows:
        return ""

    # Determine column count from the widest row
    col_count = max(len(row) for row in rows)

    # Pad rows so all have the same number of columns
    padded = []
    for row in rows:
        padded.append(row + [""] * (col_count - len(row)))

    header = padded[0]
    data_rows = padded[1:]

    lines = []
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for row in data_rows:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def parse_spreadsheet_id(url: str) -> str | None:
    """Extract spreadsheet ID from a Google Sheets URL.

    Supports URLs like:
    - https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
    - https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/

    Args:
        url: Google Sheets URL.

    Returns:
        Spreadsheet ID string, or None if not a valid Sheets URL.
    """
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    return match.group(1) if match else None
