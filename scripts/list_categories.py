import sqlite3

conn = sqlite3.connect('SLIDESHARE/db.sqlite')
cur = conn.cursor()
cur.execute("SELECT name FROM category ORDER BY LOWER(name)")
rows = [r[0] for r in cur.fetchall()]
print('Total categories:', len(rows))
print('\nSample categories (first 120):')
for name in rows[:120]:
    print(name)

# Show any matches for AI / Education / Social
print('\nMatches for AI/Education/Social:')
for name in rows:
    low = name.lower()
    if 'ai' in low or 'artificial intelligence' in low or 'education' in low or 'social' in low or 'social media' in low:
        print('-', name)
