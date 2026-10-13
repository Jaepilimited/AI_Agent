"""Run the authorized Entra sync once; verify failures preserve directory state."""
from entra_release_prepare_20260908 import ARTIFACTS, connect
from entra_release_deploy_20260908 import python

SOURCE = '''
import hashlib,json
from app.core.user_directory import sync_directory,directory_sync_status
from app.core.self_check import track_job
from app.db.mariadb import fetch_all
def fingerprint():
    rows=fetch_all("SELECT * FROM directory_users ORDER BY id")
    return hashlib.sha256(json.dumps(rows,default=str,sort_keys=True).encode()).hexdigest()
before=fingerprint()
try:
    with track_job('entra_directory_sync') as job:
        result=sync_directory()
        if not result['ok']:
            raise RuntimeError(result['error'])
        job.set_note('Entra directory users: '+str(result['synced_users']))
except RuntimeError:
    assert before==fingerprint(), 'Failed sync changed the directory'
print(json.dumps({'result':result,'status':directory_sync_status(),'unchanged_on_failure':result['ok'] or before==fingerprint()},ensure_ascii=True,default=str))
'''

if __name__ == '__main__':
    client=connect('app')
    try:
        output=python(client,SOURCE,timeout=60)
        print(output)
        (ARTIFACTS/'sync-validation.txt').write_text(output,encoding='utf-8')
    finally:
        client.close()
