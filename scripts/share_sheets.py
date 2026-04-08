#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Playwright 자동 구글시트 공유 신청.

서비스 계정 skin1004@skin1004-319714.iam.gserviceaccount.com 에
뷰어 권한 자동 추가. 로그인된 Chrome 프로필 사용.

Usage:
    python -X utf8 scripts/share_sheets.py
"""

import time
from playwright.sync_api import sync_playwright

SERVICE_ACCOUNT = "skin1004@skin1004-319714.iam.gserviceaccount.com"

# 공유 필요 14개 시트 (404 삭제 7개 + 이미 공유 JBT 1개 제외)
SHEETS = [
    {"team": "B2B1", "id": "1IkEhEUXPVKnjmSdpa99UPNjQlw2QYTvCoZCXSCBsWNI", "name": "2025 B2B1 예산안", "owner": "심재권"},
    {"team": "BCM",  "id": "1BRLCCHxaOYStZpB6Xhz2aXIOTygwnsD5WtjyEtaFDQ4", "name": "예산 & 비용 관리", "owner": "Minnie Kim"},
    {"team": "BCM",  "id": "1GSaTOt5uWj5szgtGpxvvrfjYvAabCkgZcedPsjJFY7E", "name": "2026 매출&예산 예상", "owner": "이형섭"},
    {"team": "BCM",  "id": "1Sw8o8oNqyOiRoPi_pjzIeQiPTFGqW7GB7HqntalBqqM", "name": "B2B 공급가 관리", "owner": "이해인"},
    {"team": "BCM",  "id": "1izAujxVXsD2Q5blLy3dhMWCblNhug3DjXgc9723_yws", "name": "재고 빌려주세요", "owner": "yjpark"},
    {"team": "BCM",  "id": "1w48w07Is6V6ssj4T9cVosTCsEoO0R6kRRofq5ldsrVc", "name": "제품입고일정_2025", "owner": "yjpark"},
    {"team": "BCM",  "id": "1y3iQ-CAKYe8xJp6lyjhovnsaUN4vF5sAA2mOc5qm3nY", "name": "PU_BCM_2025_수요취합", "owner": "정민균"},
    {"team": "B2B2", "id": "1PQF3JgR6G6RUV3R0dyyfLRUiEfn4ydLq-VcNbOVzx_0", "name": "계정 정보", "owner": "퇴사자관리"},
    {"team": "B2B2", "id": "1Svx64RoKzFRx0aRcoAyFHp3TK0E6LScq_aPwVGRt5cg", "name": "BCM<>SK 협업 요청", "owner": "김예슬"},
    {"team": "B2B2", "id": "1U55URCTeJrcN1LlFCM7m1AonR--k7TN74i8QQShUb5c", "name": "라벨 문안 전달 양식", "owner": "김경아"},
    {"team": "B2B2", "id": "1bwBRdSX-z-NcPI84oXJAZjKwPk_XeAc92J-1ouqzPOA", "name": "디자인 의뢰서_VMD", "owner": "노주영"},
    {"team": "B2B2", "id": "1m6M-bF-CoF6alV-AC2RtvswTPeYg8GhMwXAJg7ZT0zg", "name": "신규라벨 제작_발주", "owner": "김경아"},
    {"team": "B2B2", "id": "1qElLdqWTPxnEaTRT3_2Tfm80o-DcJcAjuV_H0OO4WFg", "name": "B2B 정보 및 규정 관리", "owner": "이해인"},
    {"team": "B2B2", "id": "1ywzlz8rZj1eLJc9hWfdVmm6AKGAG9CVEfIpoq1lV-ww", "name": "B2B 마케팅 협업 (BMP)", "owner": "Ryan Kwon"},
]


def share_sheet(page, sheet_info, idx, total):
    sid = sheet_info["id"]
    team = sheet_info["team"]
    name = sheet_info.get("name", "?")
    owner = sheet_info.get("owner", "?")
    url = f"https://docs.google.com/spreadsheets/d/{sid}/edit"

    print(f"\n[{idx}/{total}] [{team}] {name} (소유: {owner})")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(4000)

        # Case 1: Access denied → request access
        if page.locator("text=액세스 권한 요청").first.is_visible(timeout=2000):
            page.locator("text=액세스 권한 요청").first.click()
            page.wait_for_timeout(3000)
            print(f"  → 액세스 요청 전송됨 (소유자에게 알림)")
            return "REQUESTED"

        if page.locator("text=Request access").first.is_visible(timeout=1000):
            page.locator("text=Request access").first.click()
            page.wait_for_timeout(3000)
            print(f"  → Access requested")
            return "REQUESTED"

        # Case 2: We have access → Share button
        share_btn = page.locator('[aria-label="공유"]').first
        if not share_btn.is_visible(timeout=2000):
            share_btn = page.locator('[aria-label="Share"]').first
        if not share_btn.is_visible(timeout=2000):
            share_btn = page.locator('[data-tooltip*="공유"]').first
        if not share_btn.is_visible(timeout=1000):
            share_btn = page.locator('button:has-text("공유")').first

        if share_btn.is_visible(timeout=2000):
            share_btn.click()
            page.wait_for_timeout(3000)

            # Find input — try multiple selectors
            inp = page.locator('[aria-label*="사용자 및 그룹 추가"]').first
            if not inp.is_visible(timeout=2000):
                inp = page.locator('[aria-label*="Add people"]').first
            if not inp.is_visible(timeout=1000):
                inp = page.locator('input[type="text"]').first

            if inp.is_visible(timeout=2000):
                inp.click()
                inp.fill(SERVICE_ACCOUNT)
                page.wait_for_timeout(2000)

                # Press Enter to confirm email
                inp.press("Enter")
                page.wait_for_timeout(1500)

                # Change to Viewer if dropdown appears
                try:
                    role = page.locator('[aria-label*="편집자"], [aria-label*="Editor"]').first
                    if role.is_visible(timeout=1500):
                        role.click()
                        page.wait_for_timeout(500)
                        viewer = page.locator('[role="menuitem"]:has-text("뷰어")').first
                        if not viewer.is_visible(timeout=1000):
                            viewer = page.locator('[role="menuitem"]:has-text("Viewer")').first
                        if viewer.is_visible(timeout=1000):
                            viewer.click()
                            page.wait_for_timeout(500)
                except:
                    pass

                # Send
                send = page.locator('button:has-text("보내기")').first
                if not send.is_visible(timeout=1500):
                    send = page.locator('button:has-text("Send")').first
                if not send.is_visible(timeout=1000):
                    send = page.locator('button:has-text("공유")').first

                if send.is_visible(timeout=2000):
                    send.click()
                    page.wait_for_timeout(2000)

                    # Handle "send without notification" dialog if appears
                    try:
                        confirm = page.locator('button:has-text("어쨌든 공유")').first
                        if not confirm.is_visible(timeout=1500):
                            confirm = page.locator('button:has-text("Share anyway")').first
                        if confirm.is_visible(timeout=1000):
                            confirm.click()
                            page.wait_for_timeout(1000)
                    except:
                        pass

                    print(f"  → ✅ 공유 완료")
                    return "SHARED"

            print(f"  → 입력 필드 못 찾음")
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            return "DIALOG_FAIL"
        else:
            print(f"  → 공유 버튼 없음 (뷰어 권한만)")
            return "NO_SHARE_BTN"

    except Exception as e:
        print(f"  → 오류: {str(e)[:80]}")
        return "ERROR"


def main():
    print("=" * 60)
    print(f"  구글시트 공유 자동화 — {len(SHEETS)}개 시트")
    print(f"  대상: {SERVICE_ACCOUNT}")
    print("=" * 60)
    print(f"\n  Chrome이 열리면 Google 계정으로 로그인하세요.")
    print(f"  로그인 완료 후 자동으로 14개 시트에 뷰어 공유를 추가합니다.\n")

    with sync_playwright() as p:
        # Use Chrome persistent profile so login persists
        import os
        user_data = os.path.expanduser("~/.playwright-chrome-profile")
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data,
            headless=False,
            channel="chrome",
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()

        # Check Google login
        page.goto("https://docs.google.com/spreadsheets/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        if "accounts.google.com" in page.url:
            print("⚠️  Google 로그인 필요 — 브라우저에서 로그인하세요. (90초 대기)")
            for _w in range(90):
                try:
                    # Check all pages in context
                    logged_in = False
                    for p in context.pages:
                        if "accounts.google.com" not in p.url and "google.com" in p.url:
                            logged_in = True
                            break
                    if logged_in:
                        print("  ✓ 로그인 완료!")
                        break
                except:
                    pass
                time.sleep(1)
            else:
                print("  ✗ 로그인 타임아웃")
                context.close()
                return
            time.sleep(3)

        results = {"SHARED": 0, "REQUESTED": 0, "ERROR": 0, "NO_SHARE_BTN": 0, "DIALOG_FAIL": 0}

        for i, sheet in enumerate(SHEETS, 1):
            # Always use a fresh page to avoid closed page issues
            try:
                work_page = context.new_page()
                status = share_sheet(work_page, sheet, i, len(SHEETS))
                results[status] = results.get(status, 0) + 1
                work_page.close()
            except Exception as e:
                print(f"  → 페이지 오류: {str(e)[:60]}")
                results["ERROR"] = results.get("ERROR", 0) + 1

        context.close()

    print("\n" + "=" * 60)
    print("  결과")
    print("=" * 60)
    for k, v in results.items():
        if v > 0:
            print(f"  {k}: {v}건")
    print("=" * 60)


if __name__ == "__main__":
    main()
