"""Read-only capability/schema probe. No credentials or directory rows printed."""
import entra_access_count_20260908 as runner

runner.REMOTE_QUERY = r'''
import json
import requests
import pymysql
from app.config import get_settings

s = get_settings()
result = {}
try:
    response = requests.post(
        "https://login.microsoftonline.com/" + s.entra_tenant_id + "/oauth2/v2.0/token",
        data={"client_id": s.entra_client_id, "client_secret": s.entra_client_secret,
              "grant_type": "client_credentials", "scope": "https://graph.microsoft.com/.default"},
        timeout=15,
    )
    result["token_status"] = response.status_code
    token = response.json().get("access_token")
    if token:
        response = requests.get(
            "https://graph.microsoft.com/v1.0/users",
            headers={"Authorization": "Bearer " + token},
            params={"$top": 1, "$select": "id,displayName,mail,userPrincipalName,department,userType,accountEnabled"},
            timeout=15,
        )
        result["graph_status"] = response.status_code
        payload = response.json()
        result["graph_error_code"] = payload.get("error", {}).get("code")
        result["graph_fields"] = list(payload.get("value", [{}])[0]) if payload.get("value") else []
except Exception as exc:
    result["capability_error_type"] = type(exc).__name__

conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port),
                       user=s.mariadb_user, password=s.mariadb_password,
                       database=s.mariadb_database, cursorclass=pymysql.cursors.DictCursor,
                       connect_timeout=10, read_timeout=10)
try:
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        conn.begin()
        cur.execute("SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA "
                    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME IN ('ad_users','users','user_groups') ORDER BY TABLE_NAME,ORDINAL_POSITION")
        result["columns"] = cur.fetchall()
        cur.execute("SELECT TABLE_NAME,CONSTRAINT_NAME,REFERENCED_TABLE_NAME,DELETE_RULE "
                    "FROM information_schema.REFERENTIAL_CONSTRAINTS "
                    "WHERE CONSTRAINT_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME='ad_users'")
        result["foreign_keys"] = cur.fetchall()
finally:
    conn.rollback()
    conn.close()
print(json.dumps(result, default=str, ensure_ascii=True))
'''

if __name__ == "__main__":
    raise SystemExit(runner.main())
