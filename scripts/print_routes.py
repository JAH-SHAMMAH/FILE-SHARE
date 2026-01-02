import sys
sys.path.insert(0, 'SLIDESHARE')
from app.main import app
for r in app.routes:
    if r.path == '/choose-role':
        print(r.path, r.methods)
    
print('total routes', len(app.routes))
