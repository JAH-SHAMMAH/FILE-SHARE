import requests

HOST = 'http://127.0.0.1:8002'
paths = ['/', '/featured', '/upload', '/search']
for p in paths:
    try:
        r = requests.get(HOST + p, timeout=5)
        txt = r.text or ''
        found_saved = 'card__saved' in txt or 'bookmark-count' in txt or 'Saved:' in txt
        print(p, r.status_code, 'len', len(txt), 'has_saved_marker', found_saved)
    except Exception as e:
        print(p, 'ERROR', e)

# check a sample presentation and user if present
# Try to find a presentation id from the homepage (simple regex)
import re
m = re.search(r'data-pid="(\d+)"', requests.get(HOST + '/').text)
if m:
    pid = m.group(1)
    try:
        r = requests.get(HOST + f'/presentations/{pid}', timeout=5)
        print('/presentations/' + pid, r.status_code, 'len', len(r.text), 'has_bookmark', 'bookmark-count' in r.text)
    except Exception as e:
        print('present page ERROR', e)
else:
    print('No presentation id found on homepage')
