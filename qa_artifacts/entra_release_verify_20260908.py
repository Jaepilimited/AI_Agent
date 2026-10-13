"""Read production release health and aggregated directory verification."""
import json
from entra_release_prepare_20260908 import ARTIFACTS, REMOTE, connect, digest
from entra_release_deploy_20260908 import command, python, read, BACKUP, VERIFY


def main():
    selected = json.loads((ARTIFACTS / "selected.json").read_text())
    report = {}
    for target, entries in selected.items():
        client = connect(target)
        try:
            sftp = client.open_sftp()
            states = {}
            for relative, hashes in entries.items():
                data = read(sftp, REMOTE + "/" + relative)
                actual = digest(data) if data is not None else None
                states[relative] = "release" if actual == hashes["after"] else "original" if actual == hashes["before"] else "unexpected"
            sftp.close()
            report[target] = {"file_states":states}
            if target == "was":
                report[target]["service"] = command(client, "systemctl is-active ai-craver").strip()
                report[target]["health"] = json.loads(command(client, "curl --fail --silent --max-time 10 http://127.0.0.1:3000/health"))
                report[target]["login_methods"] = json.loads(command(client, "curl --fail --silent --max-time 10 http://127.0.0.1:3000/api/auth/methods"))
                result = python(client, "import json\nfrom app.db.mariadb import fetch_one\nprint(json.dumps({'directory_exists':fetch_one(\"SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='directory_migrations'\")['n']}))\n")
                exists = json.loads(result.strip().splitlines()[-1])["directory_exists"]
                report[target]["migration_table_exists"] = exists
                if exists:
                    report[target]["preservation"] = python(client, "BACKUP=" + repr(BACKUP) + "\n" + VERIFY).strip()
                    urls = ["/api/admin/directory/users", "/api/admin/directory/stats", "/api/admin/ad/users"]
                    report[target]["unauthenticated_routes"] = {url: command(client, "curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 http://127.0.0.1:3000" + url).strip() for url in urls}
                    page = command(client, "curl --fail --silent --max-time 10 http://127.0.0.1:3000/frontend/chat.html")
                    report[target]["page_has_directory_ui"] = "btn-sync-directory" in page
                    report[target]["admin_query_probe"] = python(client, "import asyncio,json\nfrom app.api.admin_group_api import list_ad_users,ad_stats\nfrom app.db.models import User\nasync def run():\n u=User(role='admin')\n rows=await list_ad_users(user=u,dept=None,search=None,group_id=None,unassigned=False,fi_only=False,visitor_only=False)\n assert all('entra_linked' in row and 'user_id' in row for row in rows)\n print(json.dumps({'active_employees':len(rows),'before_first_login':sum(row['user_id'] is None for row in rows),'admin_stats':await ad_stats(user=u)}))\nasyncio.run(run())\n").strip()
            else:
                cron = command(client, "crontab -l")
                report[target]["legacy_cron_active"] = any("scripts/sync_ad_users.py" in line and not line.lstrip().startswith("#") for line in cron.splitlines())
                report[target]["entra_cron_active"] = any("scripts/sync_entra_users.py" in line and not line.lstrip().startswith("#") for line in cron.splitlines())
        finally:
            client.close()
    print(json.dumps(report, ensure_ascii=True, default=str))
    (ARTIFACTS / "verification.json").write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
