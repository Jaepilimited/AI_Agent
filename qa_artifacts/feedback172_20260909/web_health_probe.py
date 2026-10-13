"""Read-only Web-host diagnostics; no service/configuration mutations."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sys
import time

import paramiko

ARTIFACT = Path(__file__).resolve().parent
ROOT = ARTIFACT.parents[1]

REMOTE = r'''
import datetime as dt
import http.client
import json
import re
import socket
import ssl
import subprocess

result = {'checked_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'host': socket.gethostname()}
def command(args):
    completed = subprocess.run(args, capture_output=True, text=True, timeout=8)
    return {'exit_code': completed.returncode, 'stdout': completed.stdout.strip(),
            'stderr': completed.stderr.strip()[:600]}
result['nginx_active'] = command(['systemctl', 'is-active', 'nginx'])
result['nginx_service'] = command(['systemctl', 'show', 'nginx',
    '--property=MainPID,ActiveEnterTimestamp,NRestarts,SubState,Result'])
listeners = command(['ss', '-ltn'])
result['http_listeners'] = [line for line in listeners['stdout'].splitlines()
    if re.search(r':(?:80|443|3000|8000)\s', line)]
addresses = command(['ip', '-4', '-o', 'addr', 'show'])
result['web_address_assigned'] = any('10.1.100.5/' in line for line in addresses['stdout'].splitlines())
def check(host, port, tls=False):
    connection = None
    observation = {'target': ('https://' if tls else 'http://') + host + ':' + str(port) + '/health'}
    try:
        cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
        connection = cls(host, port=port, timeout=4)
        connection.request('GET', '/health', headers={'Host': '10.1.100.5', 'Connection': 'close'})
        response = connection.getresponse()
        observation.update(status=response.status, location=response.getheader('Location'),
            server=response.getheader('Server'), content_type=response.getheader('Content-Type'),
            body=response.read(1000).decode('utf-8', 'replace'))
    except Exception as exc:
        observation.update(status=None, error_type=type(exc).__name__, error=str(exc)[:600])
    finally:
        if connection is not None:
            connection.close()
    return observation
result['local_health'] = check('127.0.0.1', 80)
result['web_address_health'] = check('10.1.100.5', 80)
if any(re.search(r':443\s', line) for line in result['http_listeners']):
    result['local_https_health'] = check('127.0.0.1', 443, tls=True)
result['upstream_was_health'] = check('10.1.150.5', 3000)
print(json.dumps(result, ensure_ascii=True))
'''


def main():
    started = time.perf_counter()
    password = os.environ.get("CRAVER_SSH_PW", "")
    if not password:
        for line in (ROOT / "scripts/_run_fx_relay.cmd").read_text(encoding="ascii").splitlines():
            if line.lower().startswith("set craver_ssh_pw="):
                password = line.partition("=")[2]
                break
    assert password, "Production SSH credential unavailable"
    result = {"checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "target_ssh_host": "10.1.100.5", "read_only": True}
    client = paramiko.SSHClient()
    client.load_host_keys(str(Path.home() / ".ssh/known_hosts"))
    try:
        client.connect("10.1.100.5", username="jeffrey", password=password,
                       allow_agent=False, look_for_keys=False,
                       timeout=10, banner_timeout=10, auth_timeout=10)
        result["ssh_connected"] = True
        stdin, stdout, stderr = client.exec_command("python3 -", timeout=45)
        stdin.write(REMOTE)
        stdin.flush()
        stdin.channel.shutdown_write()
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        result["remote_exit_code"] = code
        if code:
            result["remote_error"] = error.replace(password, "[redacted]")[-1600:]
        else:
            result["web_host_observations"] = json.loads(output)
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__,
                           "message": str(exc).replace(password, "[redacted]")[:1200]}
    finally:
        client.close()
    result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    path = ARTIFACT / "web_health_evidence.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(path), **result}, ensure_ascii=True))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
