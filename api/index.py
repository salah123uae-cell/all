import os
os.environ.setdefault('NXN_DB_PATH', '/tmp/nxn_audit.db')
from app import H, init_db
init_db()
handler = H
