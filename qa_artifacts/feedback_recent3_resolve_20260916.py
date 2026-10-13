"""Close only the three verified reports; do not send email or Jandi messages."""
import json
from pathlib import Path
import sys

import feedback_release_20260908 as access
from feedback_recent3_probe_20260916 import verify


STAGE = Path(__file__).resolve().parent / "feedback_recent3_20260916"


def resolve():
    proof = json.loads((STAGE / "production_answers.json").read_text(encoding="utf-8"))
    verify(proof)
    assert proof["health"] == {"health": 200, "ready": 200}
    release = json.loads((STAGE / "publish_result.json").read_text(encoding="utf-8"))
    assert release["published"] and release["restarted"] and release["hashes_match"]
    notes = json.loads((STAGE / "resolution_notes.json").read_text(encoding="utf-8"))
    result = access.remote("""
import json
from app.db.mariadb import fetch_all
from app.core.feedback_inbox import set_status
expected={178:14677,179:14683,180:14693}
rows=fetch_all('SELECT id,message_id,status,rating FROM message_feedback WHERE id IN (178,179,180)')
assert {r['id']:r['message_id'] for r in rows}==expected
assert all(r['rating']<0 and r['status']=='new' for r in rows),'Feedback changed during verification'
notes=""" + ascii(notes) + """
for key,note in notes.items():
 assert set_status(int(key),'done','codex',note,notify=False)
rows=fetch_all('SELECT id,status,handled_by,handled_note,handled_at FROM message_feedback WHERE id IN (178,179,180) ORDER BY id')
assert all(r['status']=='done' and r['handled_note']==notes[str(r['id'])] for r in rows)
print(json.dumps({'feedback':rows,'email_notification':False},ensure_ascii=False,default=str))
""")
    (STAGE / "resolved.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"resolved": [row["id"] for row in result["feedback"]], "email_notification": False}))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    resolve()
