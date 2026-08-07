"""모델 초상권 시트 수동 동기화.

사용법:
    python scripts/sync_model_rights.py            # 적재
    python scripts/sync_model_rights.py --dry-run  # 파싱 결과만 출력 (DB 반영 없음)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import torch  # noqa: F401  (Windows DLL 순서 — 독립 스크립트 관례)
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> None:
    dry = "--dry-run" in sys.argv
    if dry:
        import os

        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        from app.core.model_rights import SPREADSHEET_ID, parse_tab

        creds = Credentials.from_service_account_file(
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        for sh in meta["sheets"]:
            tab = sh["properties"]["title"]
            rows = (svc.spreadsheets().values()
                    .get(spreadsheetId=SPREADSHEET_ID, range=f"'{tab}'!A1:R1000")
                    .execute().get("values", []))
            models = parse_tab(rows, tab)
            print(f"\n=== 탭 {tab!r}: 모델 {len(models)}명")
            for m in models:
                print(f"  - {m['name']} ({m['line'] or '라인 미기재'}) "
                      f"기한 {len(m['periods'])}건, 사용불가={m['marked_unusable']}")
        return

    from app.core.model_rights import sync_model_rights

    stats = sync_model_rights()
    print(f"적재 완료: {stats}")


if __name__ == "__main__":
    main()
