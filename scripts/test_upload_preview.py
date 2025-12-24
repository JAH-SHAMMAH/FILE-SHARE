import os
import sys
import json
import requests

BASE = 'http://127.0.0.1:8000'


def auth_register_or_login(username, email, password):
    r = requests.post(BASE + '/api/register', json={'username': username, 'email': email, 'password': password})
    if r.status_code == 200:
        return r.json()['access_token']
    if r.status_code == 400:
        r2 = requests.post(BASE + '/api/login', json={'username': username, 'password': password})
        r2.raise_for_status()
        return r2.json()['access_token']
    r.raise_for_status()


if __name__ == '__main__':
    token = auth_register_or_login('testa', 'testa@example.test', 'pass123')
    headers = {'Authorization': f'Bearer {token}'}

    # create a tiny dummy PDF file
    fname = 'scripts/dummy.pdf'
    with open(fname, 'wb') as f:
        f.write(b'%PDF-1.1\n%Dummy PDF\n1 0 obj<<>>endobj\n')

    files = {'file': ('dummy.pdf', open(fname, 'rb'), 'application/pdf')}
    data = {'title': 'Dummy PDF', 'description': 'Test upload', 'tags': 'test', 'category': 'test'}
    r = requests.post(BASE + '/api/uploads', headers=headers, files=files, data=data)
    print('upload status', r.status_code, r.text)
    if r.status_code != 200 and r.status_code != 201:
        print('Upload failed')
        sys.exit(1)
    info = r.json()
    pid = info.get('id')
    print('Uploaded presentation id:', pid)

    # fetch preview metadata
    pv = requests.get(BASE + f'/api/presentations/{pid}/preview')
    print('preview meta', pv.status_code, pv.json())
    viewer = pv.json().get('viewer_url')
    if viewer:
        # try to GET the viewer URL
        vv = requests.get(BASE + viewer.split('#')[0])
        print('viewer GET', vv.status_code, 'content-type', vv.headers.get('Content-Type'))
    else:
        print('No viewer URL; conversion status:', pv.json().get('conversion_status'))
