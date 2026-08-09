from pathlib import Path

p = Path('app.py')
s = p.read_text(encoding='utf-8')

old = """BASE = Path(__file__).resolve().parent\nDB = Path(os.getenv('NXN_DB_PATH', BASE / 'nxn_audit.db'))\nSESSIONS: dict[str, str] = {}\n\n\ndef conn():\n    c = sqlite3.connect(DB)\n    c.row_factory = sqlite3.Row\n    c.execute('PRAGMA foreign_keys=ON')\n    return c\n\n\ndef init_db():\n    with conn() as c:\n"""

new = """BASE = Path(__file__).resolve().parent\nDATABASE_URL = os.getenv('DATABASE_URL', '').strip()\nDB = Path(os.getenv('NXN_DB_PATH', BASE / 'nxn_audit.db'))\nSESSIONS: dict[str, str] = {}\n\n\nclass CompatRow(dict):\n    def __init__(self, columns, values):\n        super().__init__(zip(columns, values))\n        self._values = tuple(values)\n    def __getitem__(self, key):\n        if isinstance(key, int):\n            return self._values[key]\n        return super().__getitem__(key)\n\n\nclass PgCursor:\n    def __init__(self, cur):\n        self.cur = cur\n        self.lastrowid = None\n    def _row(self, row):\n        if row is None:\n            return None\n        cols = [d.name for d in self.cur.description] if self.cur.description else []\n        return CompatRow(cols, row)\n    def fetchone(self):\n        return self._row(self.cur.fetchone())\n    def fetchall(self):\n        return [self._row(r) for r in self.cur.fetchall()]\n    def __iter__(self):\n        while True:\n            row = self.fetchone()\n            if row is None:\n                break\n            yield row\n\n\nclass PgConn:\n    def __init__(self):\n        import psycopg\n        self.raw = psycopg.connect(DATABASE_URL)\n    def __enter__(self):\n        return self\n    def __exit__(self, exc_type, exc, tb):\n        if exc_type:\n            self.raw.rollback()\n        else:\n            self.raw.commit()\n        self.raw.close()\n    def execute(self, sql, params=()):\n        q = sql.replace('?', '%s')\n        wants_id = q.lstrip().lower().startswith('insert into') and ' returning ' not in q.lower() and any(\n            token in q.lower() for token in ('insert into branches', 'insert into users', 'insert into audits', 'insert into corrective_actions')\n        )\n        if wants_id:\n            q = q.rstrip().rstrip(';') + ' RETURNING id'\n        cur = self.raw.cursor()\n        cur.execute(q, params)\n        out = PgCursor(cur)\n        if wants_id:\n            row = cur.fetchone()\n            out.lastrowid = row[0] if row else None\n        return out\n    def executemany(self, sql, seq):\n        cur = self.raw.cursor()\n        cur.executemany(sql.replace('?', '%s'), seq)\n        return PgCursor(cur)\n\n\ndef conn():\n    if DATABASE_URL:\n        return PgConn()\n    c = sqlite3.connect(DB)\n    c.row_factory = sqlite3.Row\n    c.execute('PRAGMA foreign_keys=ON')\n    return c\n\n\ndef init_db():\n    if DATABASE_URL:\n        with conn() as c:\n            c.execute('SELECT 1 FROM branches LIMIT 1').fetchone()\n        return\n    with conn() as c:\n"""

if old not in s:
    raise SystemExit('expected app.py block not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

req = Path('requirements.txt')
r = req.read_text(encoding='utf-8')
if 'psycopg' not in r:
    r = r.rstrip() + '\npsycopg[binary]>=3.2,<4\n'
req.write_text(r, encoding='utf-8')

api = Path('api/index.py')
a = api.read_text(encoding='utf-8')
a = a.replace("os.environ.setdefault('NXN_DB_PATH', '/tmp/nxn_audit.db')", "\nif not os.getenv('DATABASE_URL'):\n    os.environ.setdefault('NXN_DB_PATH', '/tmp/nxn_audit.db')")
api.write_text(a, encoding='utf-8')
print('PostgreSQL support enabled')
