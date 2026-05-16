import sqlite3
conn = sqlite3.connect('nmsg.db')
print('SQLite now (UTC):', conn.execute('SELECT datetime("now")').fetchone()[0])
print('SQLite now (local):', conn.execute('SELECT datetime("now","localtime")').fetchone()[0])
print()
print('=== clients ===')
for row in conn.execute('SELECT client_name, last_seen, created_at FROM clients'):
    print(row[0], '| last_seen:', row[1], '| created_at:', row[2])
print()
print('=== messages count ===')
print(conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0])
conn.close()
