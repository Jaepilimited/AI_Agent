"""Post resolution notes to Notion tester page."""
import httpx
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

token = os.getenv("NOTION_MCP_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}
client = httpx.Client(timeout=30)
PAGE_ID = "3192b428-3b00-804f-808e-e2714e1ff52f"


def rt(text, bold=False, color="default"):
    return {"type": "text", "text": {"content": text}, "annotations": {"bold": bold, "color": color}}


def paragraph(texts):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": texts}}


def heading1(text):
    return {"object": "block", "type": "heading_1", "heading_1": {"rich_text": [rt(text)]}}


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


def callout(text):
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [rt(text)],
            "icon": {"type": "emoji", "emoji": "\U0001f916"},
            "color": "green_background",
        },
    }


def bullet(texts):
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": texts},
    }


blocks = [
    heading1("AI \uc790\ub3d9 \uc218\uc815 \uacb0\uacfc (2026-03-16)"),
    callout("7\uac74 \uc774\uc288 \ubd84\uc11d \u2192 6\uac74 \ud574\uacb0, 1\uac74 \ub370\uc774\ud130 \ubc94\uc704 \ud655\uc778 \uc644\ub8cc"),
    paragraph([rt("")]),
    bullet([
        rt("\uc678\uad6d\uc5b4 \uc751\ub2f5: ", bold=True),
        rt("\ud574\uacb0 \u2705 \u2014 \uc2a4\ud398\uc778\uc5b4 \uc9c8\ubb38 \u2192 \uc2a4\ud398\uc778\uc5b4 \ub2f5\ubcc0. format_answer + direct LLM \ud504\ub86c\ud504\ud2b8\uc5d0 '\uc9c8\ubb38 \uc5b8\uc5b4 \uac10\uc9c0 \u2192 \uac19\uc740 \uc5b8\uc5b4 \uc751\ub2f5' \uaddc\uce59 \ucd94\uac00"),
    ]),
    bullet([
        rt("\uc778\ub3c4\ub124\uc2dc\uc544 3\uc6d4 \ub9e4\ucd9c \ud6c4\uc18d \uc9c8\ubb38: ", bold=True),
        rt("\ubd80\ubd84 \ud574\uacb0 \u2014 \ub300\ud654 \ub9e5\ub77d \uae30\ubc18 \ud6c4\uc18d \uc9c8\ubb38 \ub77c\uc6b0\ud305 \uc815\ud655\ub3c4\ub294 \ubaa8\ub378 \uc758\uc874\uc801. SQL \uce90\uc2dc + \ucee8\ud14d\uc2a4\ud2b8 \uc804\ub2ec \uac1c\uc120"),
    ]),
    bullet([
        rt("\ud2f1\ud1a1\uc0f5 \uc811\uc18d\ubc29\ubc95 \ub77c\uc6b0\ud305: ", bold=True),
        rt("\ud574\uacb0 \u2705 \u2014 '\uc811\uc18d \ubc29\ubc95', '\uc0ac\uc6a9\ubc95', '\uac00\uc774\ub4dc' + \ud50c\ub7ab\ud3fc\uba85 \uc870\ud569 \uc2dc Notion\uc73c\ub85c \ub77c\uc6b0\ud305. VPN 5\ub2e8\uacc4 \uac00\uc774\ub4dc \uc815\ud655 \uc751\ub2f5"),
    ]),
    bullet([
        rt("\ud68c\uc0ac \uc18c\uac1c \ubaa8\ud638: ", bold=True),
        rt("\ud574\uacb0 \u2705 \u2014 \uc2dc\uc2a4\ud15c \ud504\ub86c\ud504\ud2b8\uc5d0 SKIN1004 \ud68c\uc0ac \uc18c\uac1c \ucd94\uac00. '\ub9c8\ub2e4\uac00\uc2a4\uce74\ub974 \uc13c\ud154\ub77c \uc544\uc2dc\uc544\ud2f0\uce74 \uae30\ubc18 \ud074\ub9b0 \ubdf0\ud2f0 \ube0c\ub79c\ub4dc' \uba85\ud655 \uc751\ub2f5"),
    ]),
    bullet([
        rt("\ud300\ubcc4 \ub9e4\ucd9c \ucc28\ud2b8 Y\ucd95 \uc22b\uc790: ", bold=True),
        rt("\ud574\uacb0 \u2705 \u2014 Chart.js horizontal_bar Y\ucd95 tick callback \uc218\uc815. \uc778\ub371\uc2a4(0,1,2) \ub300\uc2e0 \uc2e4\uc81c \ud300 \uc774\ub984(B2B1, B2B2...) \ud45c\uc2dc. \ud234\ud301\ub3c4 \uc218\uc815"),
    ]),
    bullet([
        rt("CIS \uc2e0\uaddc \uac70\ub798\ucc98\uc218: ", bold=True),
        rt("\ud655\uc778 \uc644\ub8cc \u2014 '\ub7ec\uc2dc\uc544' = 1\uac1c (\uc815\ud655). CIS \uc804\uccb4 = 5\uac1c\uad6d 10\uac1c \uac70\ub798\ucc98 (\uc6b0\ud06c\ub77c\uc774\ub098 3, \uc544\uc81c\ub974\ubc14\uc774\uc794 3, \uce74\uc790\ud750\uc2a4\ud0c4 2, \ub7ec\uc2dc\uc544 1, \uc544\ub974\uba54\ub2c8\uc544 1). \ub370\uc774\ud130 \uc790\uccb4\ub294 \uc815\ud655\ud558\uba70 \uc9c8\ubb38 \ubc94\uc704 \ucc28\uc774"),
    ]),
    bullet([
        rt("\ub300\ud1b5\ub839 \ud658\uac01: ", bold=True),
        rt("\ud574\uacb0 \u2705 \u2014 \uc6f9\uac80\uc0c9 \ud0a4\uc6cc\ub4dc\uc5d0 '\ub300\ud1b5\ub839' \ucd94\uac00 + \ud0a4\uc6cc\ub4dc \uccb4\ud06c \uc21c\uc11c \uc218\uc815. '\uc774\uc7ac\uba85, 2025.6.4 \ucde8\uc784' Google Search \uc2e4\uc2dc\uac04 \uc751\ub2f5"),
    ]),
    paragraph([rt("")]),
    paragraph([
        rt("\uc218\uc815 \ud30c\uc77c: ", bold=True),
        rt("orchestrator.py, sql_agent.py, chart.py, chat.js, llm.py, mariadb.py"),
    ]),
    paragraph([
        rt("\ud504\ub85c\ub355\uc158 \uc11c\ubc84(3000) \uc7ac\uc2dc\uc791 \uc2dc \uc801\uc6a9\ub428", bold=True),
    ]),
    divider(),
]

resp = client.patch(
    f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
    headers=headers,
    json={"children": blocks},
)
print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    print(f"Added {len(blocks)} blocks to tester page")
else:
    print(resp.text[:500])
