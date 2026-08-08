from __future__ import annotations

import csv
import io
import json
import os
import secrets
import sqlite3
import urllib.parse
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = Path(os.getenv('NXN_DB_PATH', BASE / 'nxn_audit.db'))
SESSIONS: dict[str, str] = {}


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def init_db():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS branches(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name_ar TEXT NOT NULL,
          name_en TEXT NOT NULL,
          region TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('manager','auditor','branch')) DEFAULT 'auditor',
          branch_id INTEGER,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS audits(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          branch_id INTEGER NOT NULL,
          auditor_email TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('draft','submitted','reviewed','closed')) DEFAULT 'draft',
          score INTEGER CHECK(score IS NULL OR score BETWEEN 0 AND 100),
          notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS audit_sections(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title_ar TEXT NOT NULL,
          title_en TEXT NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS audit_questions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          section_id INTEGER NOT NULL,
          text_ar TEXT NOT NULL,
          text_en TEXT NOT NULL,
          weight INTEGER NOT NULL DEFAULT 1,
          sort_order INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(section_id) REFERENCES audit_sections(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS audit_answers(
          audit_id INTEGER NOT NULL,
          question_id INTEGER NOT NULL,
          answer TEXT NOT NULL CHECK(answer IN ('compliant','partial','noncompliant','na')),
          comment TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(audit_id,question_id),
          FOREIGN KEY(audit_id) REFERENCES audits(id) ON DELETE CASCADE,
          FOREIGN KEY(question_id) REFERENCES audit_questions(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS corrective_actions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          audit_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          owner TEXT NOT NULL DEFAULT '',
          due_date TEXT,
          status TEXT NOT NULL CHECK(status IN ('open','in_progress','done')) DEFAULT 'open',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(audit_id) REFERENCES audits(id) ON DELETE CASCADE
        );
        ''')
        if c.execute('SELECT COUNT(*) FROM branches').fetchone()[0] == 0:
            c.executemany('INSERT INTO branches(name_ar,name_en,region) VALUES(?,?,?)', [
                ('فرع الرياض - العليا', 'Riyadh - Olaya', 'Riyadh'),
                ('فرع جدة - الحمدانية', 'Jeddah - Al Hamdaniyah', 'Jeddah'),
                ('فرع الدمام - الفيصلية', 'Dammam - Al Faisaliyah', 'Dammam')
            ])
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
            c.executemany('INSERT INTO users(email,name,role,branch_id) VALUES(?,?,?,?)', [
                ('manager@nxn.local', 'Salah', 'manager', None),
                ('auditor@nxn.local', 'NXN Auditor', 'auditor', None),
                ('branch@nxn.local', 'Branch Manager', 'branch', 1)
            ])
        if c.execute('SELECT COUNT(*) FROM audit_sections').fetchone()[0] == 0:
            sections = [
                ('المظهر العام', 'General Appearance', 1),
                ('خدمة العملاء', 'Customer Service', 2),
                ('السلامة والامتثال', 'Safety & Compliance', 3),
                ('التشغيل', 'Operations', 4),
            ]
            c.executemany('INSERT INTO audit_sections(title_ar,title_en,sort_order) VALUES(?,?,?)', sections)
            section_ids = {r['title_en']: r['id'] for r in c.execute('SELECT id,title_en FROM audit_sections')}
            qs = [
                (section_ids['General Appearance'], 'نظافة المدخل والواجهة', 'Entrance and facade are clean', 2, 1),
                (section_ids['General Appearance'], 'ترتيب منطقة استقبال العملاء', 'Customer reception area is organized', 1, 2),
                (section_ids['Customer Service'], 'ترحيب الموظفين بالعملاء', 'Staff greet customers professionally', 2, 1),
                (section_ids['Customer Service'], 'وضوح المعلومات المقدمة للعميل', 'Information provided to customers is clear', 2, 2),
                (section_ids['Safety & Compliance'], 'مخارج الطوارئ واضحة وغير محجوبة', 'Emergency exits are clear and unobstructed', 3, 1),
                (section_ids['Safety & Compliance'], 'معدات السلامة متاحة وصالحة', 'Safety equipment is available and valid', 3, 2),
                (section_ids['Operations'], 'السجلات التشغيلية محدثة', 'Operational records are up to date', 2, 1),
                (section_ids['Operations'], 'الإجراءات اليومية مطبقة', 'Daily procedures are being followed', 2, 2),
            ]
            c.executemany('INSERT INTO audit_questions(section_id,text_ar,text_en,weight,sort_order) VALUES(?,?,?,?,?)', qs)
        if c.execute('SELECT COUNT(*) FROM audits').fetchone()[0] == 0:
            c.executemany('INSERT INTO audits(branch_id,auditor_email,status,score,notes) VALUES(?,?,?,?,?)', [
                (1, 'auditor@nxn.local', 'submitted', 88, 'Follow up required.'),
                (2, 'auditor@nxn.local', 'reviewed', 93, 'Good overall compliance.'),
                (3, 'auditor@nxn.local', 'draft', None, 'Draft visit.')
            ])


def recalc_score(c: sqlite3.Connection, audit_id: int):
    rows = c.execute('''
      SELECT q.weight,a.answer FROM audit_answers a
      JOIN audit_questions q ON q.id=a.question_id
      WHERE a.audit_id=? AND q.active=1
    ''', (audit_id,)).fetchall()
    numerator = 0
    denominator = 0
    for r in rows:
        if r['answer'] == 'na':
            continue
        w = int(r['weight'])
        denominator += 2 * w
        numerator += {'compliant': 2, 'partial': 1, 'noncompliant': 0}.get(r['answer'], 0) * w
    score = round(numerator * 100 / denominator) if denominator else None
    c.execute('UPDATE audits SET score=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (score, audit_id))
    return score


LOGIN_HTML = '''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NXN Secure Access</title><link rel="stylesheet" href="/static/style.css"></head><body><main class="login"><button class="lang floating" onclick="toggleLang()">English</button><section class="login-card"><div class="nxn">nxn</div><small>NXN SECURE ACCESS</small><h1 data-ar="تسجيل الدخول" data-en="Sign in">تسجيل الدخول</h1><p data-ar="اختر حساباً تجريبياً للدخول إلى نظام تدقيق الفروع." data-en="Choose a demo account to access the branch audit system.">اختر حساباً تجريبياً للدخول إلى نظام تدقيق الفروع.</p><form method="post" action="/login"><select name="email"><option value="manager@nxn.local">Salah — System Manager</option><option value="auditor@nxn.local">NXN Auditor — Auditor</option><option value="branch@nxn.local">Branch Manager — Branch</option></select><button class="primary" type="submit" data-ar="دخول" data-en="Sign in">دخول</button></form><div class="legal">NXN Branch Audit • Python Edition</div></section></main><script>let ar=true;function toggleLang(){ar=!ar;document.documentElement.dir=ar?'rtl':'ltr';document.querySelectorAll('[data-ar]').forEach(e=>e.textContent=e.dataset[ar?'ar':'en']);document.querySelector('.lang').textContent=ar?'English':'العربية'}</script></body></html>'''

APP_HTML = '''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NXN Branch Audit</title><link rel="stylesheet" href="/static/style.css"></head><body><main class="app" id="app"><aside><div class="nxn side-logo">nxn</div><nav id="nav"></nav><div class="side-role"><span>{initial}</span><div><b>{name}</b><small id="roleLabel">{role}</small></div></div></aside><section class="workspace"><header><h1 id="pageTitle"></h1><div class="header-actions"><button class="lang" id="langBtn">English</button><button class="lang" id="logoutBtn">خروج</button><div class="avatar">{initial}</div></div></header><div class="page" id="content"></div></section><div class="toast" id="toast" hidden></div></main><script>window.NXN_USER={user_json};</script><script src="/static/app.js"></script></body></html>'''


class H(BaseHTTPRequestHandler):
    server_version = 'NXNAudit/2.0'

    def log_message(self, fmt, *args):
        print('[NXN]', fmt % args)

    def send_bytes(self, b, typ='text/html; charset=utf-8', status=200, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', typ)
        self.send_header('Content-Length', str(len(b)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def send_text(self, s, typ='text/html; charset=utf-8', status=200, headers=None):
        self.send_bytes(s.encode(), typ, status, headers)

    def send_json(self, obj, status=200):
        self.send_text(json.dumps(obj, ensure_ascii=False, default=str), 'application/json; charset=utf-8', status)

    def redirect(self, to, headers=None):
        h = {'Location': to}
        h.update(headers or {})
        self.send_text('', status=302, headers=h)

    def body(self):
        n = int(self.headers.get('Content-Length', '0') or 0)
        raw = self.rfile.read(n) if n else b''
        if 'application/json' in self.headers.get('Content-Type', ''):
            try:
                return json.loads(raw or b'{}')
            except json.JSONDecodeError:
                return {}
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode()).items()}

    def user(self):
        jar = cookies.SimpleCookie(self.headers.get('Cookie', ''))
        sid = jar.get('nxn_session')
        email = SESSIONS.get(sid.value) if sid else None
        if not email:
            return None
        with conn() as c:
            return c.execute('SELECT * FROM users WHERE email=? AND active=1', (email,)).fetchone()

    def need_user(self):
        u = self.user()
        if not u:
            self.send_json({'error': 'unauthorized'}, 401)
            return None
        return u

    def can_access_audit(self, c, u, audit_id):
        a = c.execute('SELECT * FROM audits WHERE id=?', (audit_id,)).fetchone()
        if not a:
            return None
        if u['role'] == 'manager':
            return a
        if u['role'] == 'auditor' and a['auditor_email'] == u['email']:
            return a
        if u['role'] == 'branch' and u['branch_id'] == a['branch_id']:
            return a
        return False

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        p = parsed.path
        q = urllib.parse.parse_qs(parsed.query)
        if p.startswith('/static/'):
            f = (BASE / p.lstrip('/')).resolve()
            if BASE not in f.parents or not f.exists():
                return self.send_text('Not found', status=404)
            typ = 'text/css' if f.suffix == '.css' else 'application/javascript' if f.suffix == '.js' else 'application/octet-stream'
            return self.send_bytes(f.read_bytes(), typ)
        if p == '/health':
            return self.send_json({'status': 'ok', 'version': 2, 'database': str(DB)})
        if p == '/login':
            return self.send_text(LOGIN_HTML)
        if p == '/':
            u = self.user()
            if not u:
                return self.redirect('/login')
            user = {k: u[k] for k in ('name', 'email', 'role', 'branch_id')}
            html = APP_HTML.format(initial=u['name'][:1], name=u['name'], role=u['role'], user_json=json.dumps(user, ensure_ascii=False))
            return self.send_text(html)
        if p == '/api/session':
            u = self.user()
            return self.send_json({'authenticated': False}, 401) if not u else self.send_json({'authenticated': True, 'user': {k: u[k] for k in ('name','email','role','branch_id')}})
        if p == '/api/dashboard':
            u = self.need_user()
            if not u: return
            scope = ''
            params = []
            if u['role'] == 'auditor':
                scope, params = ' WHERE a.auditor_email=?', [u['email']]
            elif u['role'] == 'branch' and u['branch_id']:
                scope, params = ' WHERE a.branch_id=?', [u['branch_id']]
            with conn() as c:
                audits = c.execute('''SELECT a.*,b.name_ar,b.name_en,b.region FROM audits a JOIN branches b ON b.id=a.branch_id''' + scope + ' ORDER BY a.id DESC', params).fetchall()
                scored = [r['score'] for r in audits if r['score'] is not None]
                quality = round(sum(scored)/len(scored)) if scored else 0
                active_branches = c.execute('SELECT COUNT(*) FROM branches WHERE active=1').fetchone()[0]
                status_counts = {k: 0 for k in ('draft','submitted','reviewed','closed')}
                for r in audits: status_counts[r['status']] = status_counts.get(r['status'], 0) + 1

                action_sql = '''SELECT ca.*,a.branch_id,a.auditor_email,b.name_ar,b.name_en
                    FROM corrective_actions ca JOIN audits a ON a.id=ca.audit_id
                    JOIN branches b ON b.id=a.branch_id WHERE ca.status!='done' '''
                action_params = []
                if u['role'] == 'auditor': action_sql += ' AND a.auditor_email=?'; action_params=[u['email']]
                elif u['role'] == 'branch' and u['branch_id']: action_sql += ' AND a.branch_id=?'; action_params=[u['branch_id']]
                action_sql += ' ORDER BY CASE WHEN ca.due_date IS NULL THEN 1 ELSE 0 END, ca.due_date ASC, ca.id DESC'
                actions = c.execute(action_sql, action_params).fetchall()

                branch_sql = '''SELECT b.id,b.name_ar,b.name_en,b.region,COUNT(a.id) audits,ROUND(AVG(a.score)) avg_score,
                    SUM(CASE WHEN a.status IN ('submitted','reviewed','closed') THEN 1 ELSE 0 END) completed
                    FROM branches b LEFT JOIN audits a ON a.branch_id=b.id'''
                branch_params = []
                if u['role'] == 'auditor':
                    branch_sql += ' AND a.auditor_email=?'; branch_params=[u['email']]
                elif u['role'] == 'branch' and u['branch_id']:
                    branch_sql += ' WHERE b.id=?'; branch_params=[u['branch_id']]
                branch_sql += ' GROUP BY b.id ORDER BY COALESCE(avg_score,-1) DESC,b.id'
                branch_rows = c.execute(branch_sql, branch_params).fetchall()

                ans_sql = '''SELECT aa.answer,COUNT(*) n FROM audit_answers aa JOIN audits a ON a.id=aa.audit_id'''
                ans_params = []
                if u['role']=='auditor': ans_sql += ' WHERE a.auditor_email=?'; ans_params=[u['email']]
                elif u['role']=='branch' and u['branch_id']: ans_sql += ' WHERE a.branch_id=?'; ans_params=[u['branch_id']]
                ans_sql += ' GROUP BY aa.answer'
                answer_counts = {r['answer']: r['n'] for r in c.execute(ans_sql, ans_params)}

            total_answers = sum(answer_counts.values())
            compliance = round(100 * answer_counts.get('compliant',0) / total_answers) if total_answers else 0
            return self.send_json({
                'quality_score': quality,
                'audits_total': len(audits),
                'completed': sum(r['status'] in ('submitted','reviewed','closed') for r in audits),
                'open': sum(r['status'] in ('draft','submitted') for r in audits),
                'branches': active_branches if u['role']!='branch' else len(branch_rows),
                'open_actions': len(actions),
                'compliance_rate': compliance,
                'status_counts': status_counts,
                'branches_performance': [dict(r) for r in branch_rows[:6]],
                'recent_audits': [dict(r) for r in audits[:6]],
                'attention': [dict(r) for r in actions[:5]],
                'answer_counts': answer_counts
            })
        if p == '/api/branches':
            u = self.need_user()
            if not u: return
            with conn() as c:
                rows = c.execute('''SELECT b.*,COUNT(a.id) audit_count,ROUND(AVG(a.score)) avg_score FROM branches b LEFT JOIN audits a ON a.branch_id=b.id GROUP BY b.id ORDER BY b.id DESC''').fetchall()
            if u['role'] == 'branch' and u['branch_id']:
                rows = [r for r in rows if r['id'] == u['branch_id']]
            return self.send_json([dict(r) for r in rows])
        if p == '/api/audits':
            u = self.need_user()
            if not u: return
            sql = '''SELECT a.*,b.name_ar,b.name_en,b.region,(SELECT COUNT(*) FROM corrective_actions ca WHERE ca.audit_id=a.id AND ca.status!='done') open_actions FROM audits a JOIN branches b ON b.id=a.branch_id'''
            params = []
            if u['role'] == 'auditor': sql += ' WHERE a.auditor_email=?'; params = [u['email']]
            elif u['role'] == 'branch' and u['branch_id']: sql += ' WHERE a.branch_id=?'; params = [u['branch_id']]
            sql += ' ORDER BY a.id DESC'
            with conn() as c: rows = c.execute(sql, params).fetchall()
            return self.send_json([dict(r) for r in rows])
        if p.startswith('/api/audits/'):
            u = self.need_user()
            if not u: return
            try: audit_id = int(p.rsplit('/', 1)[1])
            except ValueError: return self.send_json({'error': 'not_found'}, 404)
            with conn() as c:
                a = self.can_access_audit(c, u, audit_id)
                if a is None: return self.send_json({'error':'not_found'},404)
                if a is False: return self.send_json({'error':'forbidden'},403)
                audit = c.execute('''SELECT a.*,b.name_ar,b.name_en,b.region FROM audits a JOIN branches b ON b.id=a.branch_id WHERE a.id=?''',(audit_id,)).fetchone()
                sections = []
                for s in c.execute('SELECT * FROM audit_sections ORDER BY sort_order,id'):
                    questions = c.execute('''SELECT q.*,aa.answer,aa.comment FROM audit_questions q LEFT JOIN audit_answers aa ON aa.question_id=q.id AND aa.audit_id=? WHERE q.section_id=? AND q.active=1 ORDER BY q.sort_order,q.id''',(audit_id,s['id'])).fetchall()
                    sections.append({**dict(s), 'questions':[dict(x) for x in questions]})
                actions = c.execute('SELECT * FROM corrective_actions WHERE audit_id=? ORDER BY id DESC',(audit_id,)).fetchall()
            return self.send_json({'audit':dict(audit),'sections':sections,'actions':[dict(x) for x in actions]})
        if p == '/api/users':
            u = self.need_user()
            if not u: return
            if u['role'] != 'manager': return self.send_json({'error':'forbidden'},403)
            with conn() as c: rows = c.execute('''SELECT u.*,b.name_ar branch_name_ar,b.name_en branch_name_en FROM users u LEFT JOIN branches b ON b.id=u.branch_id ORDER BY u.id DESC''').fetchall()
            return self.send_json([dict(r) for r in rows])
        if p in ('/api/reports','/api/reports.csv'):
            u = self.need_user()
            if not u: return
            with conn() as c:
                rows = c.execute('''SELECT b.id,b.name_ar,b.name_en,b.region,COUNT(a.id) audits,ROUND(AVG(a.score)) avg_score,SUM(CASE WHEN a.status IN ('submitted','reviewed','closed') THEN 1 ELSE 0 END) completed FROM branches b LEFT JOIN audits a ON a.branch_id=b.id GROUP BY b.id ORDER BY avg_score DESC''').fetchall()
            if u['role'] == 'branch' and u['branch_id']: rows = [r for r in rows if r['id']==u['branch_id']]
            if p.endswith('.csv'):
                out=io.StringIO(); w=csv.writer(out); w.writerow(['id','name_ar','name_en','region','audits','completed','avg_score'])
                for r in rows: w.writerow([r[k] for k in ('id','name_ar','name_en','region','audits','completed','avg_score')])
                return self.send_bytes(out.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=nxn_branch_report.csv'})
            return self.send_json([dict(r) for r in rows])
        self.send_text('Not found', status=404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        if p == '/login':
            email = self.body().get('email','').strip().lower()
            with conn() as c: u = c.execute('SELECT * FROM users WHERE email=? AND active=1',(email,)).fetchone()
            if not u: return self.send_text(LOGIN_HTML.replace('</form>','<p class="error">Account not found.</p></form>'),status=401)
            sid = secrets.token_urlsafe(24); SESSIONS[sid] = email
            return self.redirect('/', {'Set-Cookie':f'nxn_session={sid}; Path=/; HttpOnly; SameSite=Lax'})
        if p == '/logout':
            jar = cookies.SimpleCookie(self.headers.get('Cookie','')); sid = jar.get('nxn_session')
            if sid: SESSIONS.pop(sid.value,None)
            return self.send_json({'ok':True})
        if p == '/api/branches':
            u = self.need_user()
            if not u: return
            if u['role']!='manager': return self.send_json({'error':'forbidden'},403)
            d=self.body()
            if any(not str(d.get(k,'')).strip() for k in ('name_ar','name_en','region')): return self.send_json({'error':'missing_fields'},400)
            with conn() as c:
                cur=c.execute('INSERT INTO branches(name_ar,name_en,region) VALUES(?,?,?)',(d['name_ar'].strip(),d['name_en'].strip(),d['region'].strip()))
                r=c.execute('SELECT * FROM branches WHERE id=?',(cur.lastrowid,)).fetchone()
            return self.send_json(dict(r),201)
        if p == '/api/audits':
            u=self.need_user()
            if not u: return
            if u['role']=='branch': return self.send_json({'error':'forbidden'},403)
            d=self.body(); bid=d.get('branch_id')
            if not bid: return self.send_json({'error':'branch_required'},400)
            try: bid=int(bid)
            except: return self.send_json({'error':'invalid_branch'},400)
            with conn() as c:
                if not c.execute('SELECT 1 FROM branches WHERE id=? AND active=1',(bid,)).fetchone(): return self.send_json({'error':'invalid_branch'},400)
                auditor=u['email'] if u['role']=='auditor' else (d.get('auditor_email') or u['email'])
                cur=c.execute('INSERT INTO audits(branch_id,auditor_email,status,notes) VALUES(?,?,?,?)',(bid,auditor,'draft',str(d.get('notes',''))))
                r=c.execute('SELECT * FROM audits WHERE id=?',(cur.lastrowid,)).fetchone()
            return self.send_json(dict(r),201)
        if p == '/api/users':
            u=self.need_user()
            if not u:return
            if u['role']!='manager': return self.send_json({'error':'forbidden'},403)
            d=self.body(); role=d.get('role','auditor')
            if not d.get('email') or not d.get('name'): return self.send_json({'error':'missing_fields'},400)
            if role not in ('manager','auditor','branch'): return self.send_json({'error':'invalid_role'},400)
            branch_id=d.get('branch_id') or None
            if role=='branch' and not branch_id: return self.send_json({'error':'branch_required'},400)
            try:
                with conn() as c:
                    cur=c.execute('INSERT INTO users(email,name,role,branch_id) VALUES(?,?,?,?)',(d['email'].strip().lower(),d['name'].strip(),role,branch_id))
                    r=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone()
            except sqlite3.IntegrityError: return self.send_json({'error':'email_exists'},409)
            return self.send_json(dict(r),201)
        if p.endswith('/answers') and p.startswith('/api/audits/'):
            u=self.need_user()
            if not u:return
            try: audit_id=int(p.split('/')[3])
            except: return self.send_json({'error':'not_found'},404)
            d=self.body(); qid=d.get('question_id'); answer=d.get('answer')
            if answer not in ('compliant','partial','noncompliant','na'): return self.send_json({'error':'invalid_answer'},400)
            with conn() as c:
                a=self.can_access_audit(c,u,audit_id)
                if a is None:return self.send_json({'error':'not_found'},404)
                if a is False:return self.send_json({'error':'forbidden'},403)
                if u['role']=='branch': return self.send_json({'error':'forbidden'},403)
                if a['status'] in ('reviewed','closed') and u['role']!='manager': return self.send_json({'error':'audit_locked'},409)
                if not c.execute('SELECT 1 FROM audit_questions WHERE id=? AND active=1',(qid,)).fetchone(): return self.send_json({'error':'invalid_question'},400)
                c.execute('''INSERT INTO audit_answers(audit_id,question_id,answer,comment) VALUES(?,?,?,?) ON CONFLICT(audit_id,question_id) DO UPDATE SET answer=excluded.answer,comment=excluded.comment,updated_at=CURRENT_TIMESTAMP''',(audit_id,qid,answer,str(d.get('comment',''))))
                score=recalc_score(c,audit_id)
            return self.send_json({'ok':True,'score':score})
        if p.endswith('/actions') and p.startswith('/api/audits/'):
            u=self.need_user()
            if not u:return
            try:audit_id=int(p.split('/')[3])
            except:return self.send_json({'error':'not_found'},404)
            d=self.body(); title=str(d.get('title','')).strip()
            if not title:return self.send_json({'error':'title_required'},400)
            with conn() as c:
                a=self.can_access_audit(c,u,audit_id)
                if a is None:return self.send_json({'error':'not_found'},404)
                if a is False:return self.send_json({'error':'forbidden'},403)
                cur=c.execute('INSERT INTO corrective_actions(audit_id,title,owner,due_date,status) VALUES(?,?,?,?,?)',(audit_id,title,str(d.get('owner','')).strip(),d.get('due_date') or None,'open'))
                r=c.execute('SELECT * FROM corrective_actions WHERE id=?',(cur.lastrowid,)).fetchone()
            return self.send_json(dict(r),201)
        self.send_text('Not found',status=404)

    def do_PATCH(self):
        p=urllib.parse.urlparse(self.path).path
        u=self.need_user()
        if not u:return
        if p.startswith('/api/audits/') and p.count('/')==3:
            try:audit_id=int(p.rsplit('/',1)[1])
            except:return self.send_json({'error':'not_found'},404)
            d=self.body(); allowed_status=('draft','submitted','reviewed','closed')
            with conn() as c:
                a=self.can_access_audit(c,u,audit_id)
                if a is None:return self.send_json({'error':'not_found'},404)
                if a is False:return self.send_json({'error':'forbidden'},403)
                if u['role']=='branch':return self.send_json({'error':'forbidden'},403)
                status=d.get('status',a['status'])
                if status not in allowed_status:return self.send_json({'error':'invalid_status'},400)
                if status in ('reviewed','closed') and u['role']!='manager':return self.send_json({'error':'manager_required'},403)
                c.execute('UPDATE audits SET status=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(status,str(d.get('notes',a['notes'])),audit_id))
                r=c.execute('SELECT * FROM audits WHERE id=?',(audit_id,)).fetchone()
            return self.send_json(dict(r))
        if p.startswith('/api/actions/'):
            try:aid=int(p.rsplit('/',1)[1])
            except:return self.send_json({'error':'not_found'},404)
            d=self.body(); status=d.get('status')
            if status not in ('open','in_progress','done'):return self.send_json({'error':'invalid_status'},400)
            with conn() as c:
                ca=c.execute('SELECT * FROM corrective_actions WHERE id=?',(aid,)).fetchone()
                if not ca:return self.send_json({'error':'not_found'},404)
                a=self.can_access_audit(c,u,ca['audit_id'])
                if a is False:return self.send_json({'error':'forbidden'},403)
                c.execute('UPDATE corrective_actions SET status=? WHERE id=?',(status,aid))
                r=c.execute('SELECT * FROM corrective_actions WHERE id=?',(aid,)).fetchone()
            return self.send_json(dict(r))
        return self.send_json({'error':'not_found'},404)


if __name__ == '__main__':
    init_db()
    port=int(os.getenv('PORT','5000'))
    print(f'NXN Branch Audit v2 running on http://127.0.0.1:{port}')
    ThreadingHTTPServer(('0.0.0.0',port),H).serve_forever()
