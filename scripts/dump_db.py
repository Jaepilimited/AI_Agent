import pymysql

conn = pymysql.connect(host='127.0.0.1', port=3306, user='skin1004', password='skin1004!', database='skin1004_ai', charset='utf8mb4')
cursor = conn.cursor()

output = []
output.append('SET FOREIGN_KEY_CHECKS=0;')
output.append('SET NAMES utf8mb4;')

cursor.execute('SHOW TABLES')
tables = [row[0] for row in cursor.fetchall()]
print(f'Tables: {tables}')

for table in tables:
    output.append(f'DROP TABLE IF EXISTS `{table}`;')
    cursor.execute(f'SHOW CREATE TABLE `{table}`')
    create = cursor.fetchone()[1]
    output.append(create + ';')

    cursor.execute(f'SELECT * FROM `{table}`')
    rows = cursor.fetchall()
    if rows:
        for row in rows:
            values = []
            for v in row:
                if v is None:
                    values.append('NULL')
                elif isinstance(v, (int, float)):
                    values.append(str(v))
                else:
                    escaped = str(v).replace("\\", "\\\\").replace("'", "\\'")
                    values.append(f"'{escaped}'")
            output.append(f'INSERT INTO `{table}` VALUES ({", ".join(values)});')

output.append('SET FOREIGN_KEY_CHECKS=1;')

with open('C:/Users/DB_PC/Desktop/skin1004_ai_backup.sql', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))

print('Done!')
for table in tables:
    cursor.execute(f'SELECT COUNT(*) FROM `{table}`')
    print(f'  {table}: {cursor.fetchone()[0]} rows')

cursor.close()
conn.close()
