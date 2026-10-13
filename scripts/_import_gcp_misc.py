"""GCP misc → 로컬 병합: users 필드, user_groups, sql_cache, audit_logs (임시)"""
import sys, io, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pymysql
from app.config import get_settings

s = get_settings()
conn = pymysql.connect(host=s.mariadb_host, port=int(s.mariadb_port), user=s.mariadb_user,
                       password=s.mariadb_password, database=s.mariadb_database,
                       charset='utf8mb4', autocommit=False, cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()

with open("data/gcp_misc_export.json", encoding="utf-8") as f:
    misc = json.load(f)
with open("data/gcp_convo_export.json", encoding="utf-8") as f:
    gcp_users = json.load(f)["users"]

# ── 0) ad_users id 일치 검증 맵 (GCP id→username vs 로컬) ──
cur.execute("SELECT id, username FROM ad_users")
local_ad = {r["id"]: r["username"] for r in cur.fetchall()}
gcp_ad = {r["id"]: r["username"] for r in misc["ad_users_ref"]}
ad_id_ok = {i for i, u in gcp_ad.items() if local_ad.get(i) == u}
print(f"ad_users id 일치: {len(ad_id_ok)}/{len(gcp_ad)}")

# ── 1) users 필드 병합 (GCP가 운영 원본이던 기간의 계정 정보) ──
cur.execute("SELECT * FROM users")
local_users = {r["id"]: r for r in cur.fetchall()}
upd_pw, upd_ad, upd_misc = 0, 0, 0
for g in gcp_users:
    lu = local_users.get(g["id"])
    if not lu:
        continue
    sets, vals = [], []
    # 비밀번호: GCP 값 우선 (id=1 주인님은 로컬 로그인 유지)
    if g["id"] != 1 and g.get("password_hash") and g["password_hash"] != lu.get("password_hash"):
        sets.append("password_hash=%s"); vals.append(g["password_hash"]); upd_pw += 1
    # ad_user_id: 로컬 NULL이고 GCP 값이 검증된 경우만
    if lu.get("ad_user_id") is None and g.get("ad_user_id") in ad_id_ok:
        sets.append("ad_user_id=%s"); vals.append(g["ad_user_id"]); upd_ad += 1
    # role / allowed_models / is_active: GCP 관리값 반영 (id=1 제외)
    if g["id"] != 1:
        for col in ("role", "allowed_models", "is_active"):
            if col in g and g[col] != lu.get(col):
                sets.append(f"{col}=%s"); vals.append(g[col]); upd_misc += 1
    if sets:
        vals.append(g["id"])
        cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=%s", vals)
print(f"users: password={upd_pw}, ad_link={upd_ad}, role/models/active 필드={upd_misc}")

# ── 2) access_groups 검증 (이름 기준 동일성) ──
cur.execute("SELECT id, name FROM access_groups")
local_groups = {r["id"]: r["name"] for r in cur.fetchall()}
gcp_groups = {r["id"]: r["name"] for r in misc["access_groups"]}
group_ok = {i for i, n in gcp_groups.items() if local_groups.get(i) == n}
print(f"access_groups id 일치: {len(group_ok)}/{len(gcp_groups)}")

# ── 3) user_groups ──
ug_new, ug_skip = 0, 0
for r in misc["user_groups"]:
    if r["ad_user_id"] in ad_id_ok and r["group_id"] in group_ok:
        ug_new += cur.execute(
            "INSERT IGNORE INTO user_groups (ad_user_id, group_id) VALUES (%s, %s)",
            (r["ad_user_id"], r["group_id"]))
    else:
        ug_skip += 1
print(f"user_groups: +{ug_new}, 스킵={ug_skip} (총 {len(misc['user_groups'])})")

# ── 4) sql_cache (query_hash 유니크로 중복 무시) ──
if misc["sql_cache"]:
    cols = [c for c in misc["sql_cache"][0].keys() if c != "id"]
    sc_new = 0
    for r in misc["sql_cache"]:
        sc_new += cur.execute(
            f"INSERT IGNORE INTO sql_cache ({', '.join(cols)}) VALUES ({', '.join(['%s']*len(cols))})",
            [r[c] for c in cols])
    print(f"sql_cache: +{sc_new} (총 {len(misc['sql_cache'])})")

# ── 5) audit_logs (5/5 이후 GCP 이벤트, id 재발급) ──
if misc["audit_logs"]:
    cols = [c for c in misc["audit_logs"][0].keys() if c != "id"]
    al_new = 0
    for r in misc["audit_logs"]:
        al_new += cur.execute(
            f"INSERT INTO audit_logs ({', '.join(cols)}) VALUES ({', '.join(['%s']*len(cols))})",
            [r[c] for c in cols])
    print(f"audit_logs: +{al_new} (총 {len(misc['audit_logs'])})")

conn.commit()
print("커밋 완료")
