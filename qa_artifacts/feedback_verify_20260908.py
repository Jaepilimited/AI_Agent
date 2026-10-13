"""Verify deployed feedback fixes with the reporter's original Calendar query."""
import json
from pathlib import Path
import sys

import feedback_release_20260908 as release

CODE = r'''
import asyncio, json, subprocess
import requests
from app.config import get_settings
from app.db.mariadb import fetch_all, fetch_one
from app.agents.gws_agent import GWSAgent

s = get_settings()
result = {'service': subprocess.run(['systemctl', 'is-active', 'ai-craver'], capture_output=True, text=True).stdout.strip()}
for label, url in [('was', 'http://127.0.0.1:3000/health')]:
    try:
        response = requests.get(url, timeout=8)
        result[label + '_health'] = response.status_code
    except requests.RequestException as exc:
        result[label + '_health_error'] = type(exc).__name__
response = requests.get('http://127.0.0.1:3000/frontend/chat.js', timeout=8)
result['served_chart_fix'] = response.status_code == 200 and '_sameYearDaily' in response.text and 'pointRadius = 2.5' in response.text
result['public_redirect'] = getattr(s, 'entra_redirect_uri', '')
row = fetch_one(
    "SELECT u.email, f.conversation_id, f.message_id FROM message_feedback f "
    "JOIN users u ON u.id=f.user_id "
    "WHERE f.id=167 AND DATE(f.created_at)='2026-09-08'")
question = fetch_one("SELECT content FROM messages WHERE conversation_id=%s "
                     "AND role='user' AND id<%s ORDER BY id DESC LIMIT 1",
                     (row['conversation_id'], row['message_id']))
row['question'] = question['content']
assert row and row['email'] and row['question'], 'Original report not found'
async def check():
    agent = GWSAgent()
    original = await agent.run(row['question'], user_email=row['email'])
    teams = await agent.run('올해 캘린더 미팅 횟수와 팀별 협업 통계', user_email=row['email'])
    return {'original_answer': original, 'team_answer': teams}
result.update(asyncio.run(check()))
result['feedback'] = fetch_all("SELECT id,status,DATE(created_at) AS created_on,handled_at "
                              "FROM message_feedback WHERE id IN (166,167) ORDER BY id")
print(json.dumps(result, ensure_ascii=True, default=str))
'''

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    result = release.remote(CODE)
    (Path(__file__).resolve().parent / 'feedback166_167_verification.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    displayed = dict(result)
    displayed['team_answer'] = result['team_answer'][:2300]
    print(json.dumps(displayed, ensure_ascii=False, indent=2))
