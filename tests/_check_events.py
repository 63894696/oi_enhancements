import sqlite3, os
db = os.path.join(os.path.dirname(os.path.abspath('prisIragent_web.py')), 'prisIr_calendar_data', 'calendar.db')
conn = sqlite3.connect(db)
for r in conn.execute("SELECT event_id, summary, source, dismissed, dtstart_utc, dtend_utc FROM events"):
    print(r)
print('---count---', conn.execute('SELECT COUNT(*) FROM events').fetchone()[0])
