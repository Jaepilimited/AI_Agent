"""Probe Graph locally using an in-memory application token from the WAS."""
import json
from pathlib import Path
import paramiko
import requests

runner = Path("scripts/_run_fx_relay.cmd")
password = next(line.partition("=")[2] for line in runner.read_text(encoding="ascii").splitlines()
                if line.lower().startswith("set craver_ssh_pw="))
client = paramiko.SSHClient()
client.load_host_keys(str(Path.home() / ".ssh" / "known_hosts"))
try:
    client.connect("10.1.150.5", username="jeffrey", password=password,
                   allow_agent=False, look_for_keys=False, timeout=10)
    command = ("cd /home/jeffrey/AI_Agent && set -a && . ./.env && set +a && "
               "export HTTPS_PROXY=http://10.1.50.2:3128 HTTP_PROXY=http://10.1.50.2:3128 && "
               "./venv/bin/python -")
    stdin, stdout, stderr = client.exec_command(command, timeout=30)
    stdin.write("""
import json, requests
from app.config import get_settings
s=get_settings()
r=requests.post('https://login.microsoftonline.com/'+s.entra_tenant_id+'/oauth2/v2.0/token',
 data={'client_id':s.entra_client_id,'client_secret':s.entra_client_secret,
 'grant_type':'client_credentials','scope':'https://graph.microsoft.com/.default'},timeout=15)
print(json.dumps({'status':r.status_code,'token':r.json().get('access_token','')}))
""")
    stdin.flush()
    stdin.channel.shutdown_write()
    result = json.loads(stdout.read().decode())
finally:
    client.close()
output = {"token_status": result["status"]}
if result.get("token"):
    response = requests.get("https://graph.microsoft.com/v1.0/users",
                            headers={"Authorization": "Bearer " + result["token"]},
                            params={"$top": 1, "$select": "id,displayName,mail,userPrincipalName,department,userType,accountEnabled"},
                            timeout=15)
    output["graph_status"] = response.status_code
    payload = response.json()
    output["error_code"] = payload.get("error", {}).get("code")
    output["returned_fields"] = list(payload.get("value", [{}])[0]) if payload.get("value") else []
print(json.dumps(output, ensure_ascii=True))
