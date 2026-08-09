import os
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ['DATABASE_URL']

class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class Cursor:
    def __init__(self, cur, lastrowid=None):
        self.cur = cur
        self.lastrowid = lastrowid
    def fetchone(self):
        row = self.cur.fetchone()
        return Row(row) if row is not None else None
    def fetchall(self):
        return [Row(row) for row in self.cur.fetchall()]
    def __iter__(self):
        for row in self.cur:
            yield Row(row)

class Connection:
    def __init__(self):
        self.db = psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None)
    def __enter__(self):
        return self
    def __exit__(self, typ, value, tb):
        try:
            self.db.commit() if typ is None else self.db.rollback()
        finally:
            self.db.close()
    def execute(self, sql, params=()):
        sql = sql.replace('?', '%s').replace('active=1', 'active=TRUE').replace('active = 1', 'active = TRUE')
        low = sql.lstrip().lower()
        insert_prefixes = ('insert into branches','insert into users','insert into audits','insert into corrective_actions')
        needs_id = low.startswith(insert_prefixes) and ' returning ' not in low
        if needs_id:
            sql = sql.rstrip().rstrip(';') + ' RETURNING id'
        cur = self.db.cursor()
        cur.execute(sql, params)
        lastrowid = None
        if needs_id:
            row = cur.fetchone()
            lastrowid = row['id'] if row else None
        return Cursor(cur, lastrowid)

def conn():
    return Connection()
