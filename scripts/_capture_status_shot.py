#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""System Status 화면 한 장만 다시 찍는다 (PPT `docs/guide_assets/ui_status.png` 교체용).

프로덕션(10.1.100.5)은 DB_PC 에서 :80 이 막혀 있어 로컬 dev(3001)로 찍는다 —
소스 목록·상태는 같은 코드에서 나온다.

실행: <python311> scripts/_capture_status_shot.py [출력경로]
"""

import asyncio
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from playwright.async_api import async_playwright

BASE = os.environ.get("CELLA_BASE", "http://127.0.0.1:3001")
NAME = os.environ.get("CELLA_NAME", "임재필")
PW = os.environ.get("CELLA_PW", "jrj2002")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "guide_assets", "ui_status.png")
DEBUG = os.path.join(ROOT, "docs", "guide_assets", "_debug")
os.makedirs(DEBUG, exist_ok=True)


async def main():
    async with async_playwright() as pw:
        br = await pw.chromium.launch(headless=True)
        ctx = await br.new_context(viewport={"width": 1440, "height": 900},
                                   device_scale_factor=1)
        page = await ctx.new_page()

        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(1200)
        await page.screenshot(path=os.path.join(DEBUG, "01_login.png"))
        print("url:", page.url)

        # ── 로그인 (이름 → 부서 자동 → 비밀번호) ──
        name = page.locator("#input-name")
        if await name.count():
            await name.fill("")
            await name.type(NAME, delay=60)
            # 이름 자동완성 항목을 눌러야 소속 팀 select 가 채워진다 (auth.js selectUser)
            item = page.locator(".ac-item").first
            await item.wait_for(state="visible", timeout=8000)
            await item.click()
            await page.wait_for_timeout(600)
            dept = page.locator("#input-dept")
            opts = await dept.locator("option").all()
            print("dept options:", [(await o.get_attribute("value")) for o in opts])
            await page.locator("#input-password").fill(PW)
            await page.screenshot(path=os.path.join(DEBUG, "02_filled.png"))
            await page.locator("#btn-submit").click()
            await page.wait_for_timeout(6000)
        print("after login:", page.url)
        await page.screenshot(path=os.path.join(DEBUG, "03_chat.png"))

        if "/login" in page.url:
            err = page.locator("#error-msg, .error-msg").first
            msg = (await err.inner_text()).strip() if await err.count() else ""
            print("!! 로그인 실패:", msg or "(사유 없음)")
            await br.close()
            sys.exit(1)

        # ── System Status 드로어 열기 (chat.html: #btn-system-status → #skin-status-drawer) ──
        await page.locator("#btn-system-status").click()
        drawer = page.locator("#skin-status-drawer")
        await drawer.wait_for(state="visible", timeout=10000)
        # 소스 목록이 채워질 때까지 (상태 배지가 하나라도 뜰 때까지)
        try:
            await page.wait_for_function(
                "() => document.querySelectorAll('#skin-status-drawer .src-item, "
                "#skin-status-drawer li, #skin-status-drawer .status-row').length > 5",
                timeout=15000)
        except Exception:  # noqa: BLE001
            print("(소스 행 대기 타임아웃 — 그대로 찍는다)")
        await page.wait_for_timeout(3000)
        await page.screenshot(path=OUT)
        print("saved:", OUT)
        await br.close()


asyncio.run(main())
