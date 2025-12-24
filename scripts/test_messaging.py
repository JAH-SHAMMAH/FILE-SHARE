import json
import sys
from urllib import request, error

BASE = 'http://127.0.0.1:8000'

headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}


def post_json(path, payload, token=None):
    url = BASE + path
    data = json.dumps(payload).encode('utf-8')
    h = dict(headers)
    if token:
        h['Authorization'] = f'Bearer {token}'
    req = request.Request(url, data=data, headers=h, method='POST')
    try:
        with request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except error.HTTPError as e:
        try:
            body = e.read().decode('utf-8')
            return {'_error': e.code, 'body': body}
        except Exception:
            return {'_error': e.code}
    except Exception as e:
        return {'_error': str(e)}


def get_json(path, token=None):
    url = BASE + path
    h = {'Accept': 'application/json'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    req = request.Request(url, headers=h)
    try:
        with request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except error.HTTPError as e:
        try:
            return {'_error': e.code, 'body': e.read().decode('utf-8')}
        except Exception:
            return {'_error': e.code}
    except Exception as e:
        return {'_error': str(e)}


if __name__ == '__main__':
    a = post_json('/api/register', {'username': 'testa', 'email': 'testa@example.test', 'password': 'pass123'})
    if '_error' in a and (a.get('_error') == 400 or (isinstance(a.get('_error'), dict))):
        # try login
        a = post_json('/api/login', {'username': 'testa', 'password': 'pass123'})
    b = post_json('/api/register', {'username': 'testb', 'email': 'testb@example.test', 'password': 'pass123'})
    if '_error' in b and (b.get('_error') == 400 or (isinstance(b.get('_error'), dict))):
        b = post_json('/api/login', {'username': 'testb', 'password': 'pass123'})

    print('A auth response:', a)
    print('B auth response:', b)
    a_token = a.get('access_token')
    b_token = b.get('access_token')
    if not a_token or not b_token:
        print('Failed to get tokens; aborting')
        sys.exit(1)

    a_me = get_json('/api/me', token=a_token)
    b_me = get_json('/api/me', token=b_token)
    print('A me:', a_me)
    print('B me:', b_me)

    a_id = a_me.get('id')
    b_id = b_me.get('id')
    if not a_id or not b_id:
        print('Missing ids; aborting')
        sys.exit(1)

    # send message A -> B
    res = post_json(f'/api/messages/{b_id}', {'content': 'Hello from A'}, token=a_token)
    print('Send message result:', res)

    # fetch history as B (messages with A)
    hist = get_json(f'/api/messages/{a_id}', token=b_token)
    print('History fetched by B (should include message):', hist)

    # fetch unread_counts as B
    unread = get_json('/api/messages/unread_counts', token=b_token)
    print('Unread counts for B:', unread)
