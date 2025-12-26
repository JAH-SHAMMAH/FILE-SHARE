import requests

def run():
    url = 'http://127.0.0.1:8000/api/uploads'
    files = {'file': ('test_upload.pdf', open('SLIDESHARE/uploads/test_upload.pdf', 'rb'), 'application/pdf')}
    data = {'title': 'Automated Test Upload', 'description': 'Uploaded by automation'}
    resp = requests.post(url, files=files, data=data)
    print('STATUS', resp.status_code)
    try:
        print(resp.json())
    except Exception:
        print(resp.text)


if __name__ == '__main__':
    run()
