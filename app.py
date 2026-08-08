from __future__ import annotations

import os
import sqlite3
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("NXN_DB_PATH", BASE_DIR / "nxn_audit.db"))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "nxn-dev-change-me")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS branches(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name_ar TEXT NOT NULL,
          name_en TEXT NOT NULL,
          region TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('manager','auditor','branch')),
          branch_id INTEGER,
          active INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS audits(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          branch_id INTEGER NOT NULL,
          auditor_email TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','reviewed','closed')),
          score INTEGER CHECK(score IS NULL OR score BETWEEN 0 AND 100),
          notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE CASCADE
        );
        """)
        if c.execute("SELECT COUNT(*) FROM branches").fetchone()[0] == 0:
            c.executemany("INSERT INTO branches(name_ar,name_en,region) VALUES(?,?,?)", [
                ("فرع دبي مول", "Dubai Mall", "Dubai"),
                ("فرع أبوظبي مول", "Abu Dhabi Mall", "Abu Dhabi"),
                ("فرع الشارقة", "Sharjah Branch", "Sharjah"),
                ("فرع العين", "Al Ain Branch", "Al Ain"),
            ])
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            c.executemany("INSERT INTO users(email,name,role,branch_id) VALUES(?,?,?,?)", [
                ("manager@nxn.local", "Salah", "manager", None),
                ("auditor@nxn.local", "NXN Auditor", "auditor", None),
                ("branch@nxn.local", "Branch Manager", "branch", 1),
            ])
        if c.execute("SELECT COUNT(*) FROM audits").fetchone()[0] == 0:
            c.executemany("INSERT INTO audits(branch_id,auditor_email,status,score,notes) VALUES(?,?,?,?,?)", [
                (1, "auditor@nxn.local", "submitted", 96, "Strong compliance."),
                (2, "auditor@nxn.local", "reviewed", 92, "Minor follow-up required."),
                (3, "auditor@nxn.local", "closed", 88, "Actions completed."),
                (4, "auditor@nxn.local", "draft", None, "Draft visit."),
            ])


def current_user():
    email = session.get("email")
    if not email:
        return None
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE email=? AND active=1", (email,)).fetchone()
        return dict(row) if row else None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "nxn-branch-audit"})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        with db() as c:
            row = c.execute("SELECT * FROM users WHERE email=? AND active=1", (email,)).fetchone()
        if not row:
            return render_template("login.html", error="Invalid account"), 400
        session["email"] = email
        return redirect(url_for("index"))
    if current_user():
        return redirect(url_for("index"))
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    return render_template("index.html", user=current_user())


@app.get("/api/dashboard")
@login_required
def dashboard():
    user = current_user()
    with db() as c:
        params = []
        where = ""
        if user["role"] == "branch" and user.get("branch_id"):
            where = " WHERE a.branch_id=?"
            params.append(user["branch_id"])
        audits = c.execute(
            "SELECT a.*,b.name_ar,b.name_en FROM audits a JOIN branches b ON b.id=a.branch_id" + where + " ORDER BY a.id DESC",
            params,
        ).fetchall()
        if user["role"] == "branch" and user.get("branch_id"):
            branches = c.execute("SELECT * FROM branches WHERE id=?", (user["branch_id"],)).fetchall()
        else:
            branches = c.execute("SELECT * FROM branches ORDER BY id").fetchall()
    scores = [r["score"] for r in audits if r["score"] is not None]
    return jsonify({
        "user": user,
        "kpis": {
            "branches": len(branches),
            "audits": len(audits),
            "average_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "open_audits": sum(1 for r in audits if r["status"] in {"draft", "submitted"}),
        },
        "branches": [dict(r) for r in branches],
        "audits": [dict(r) for r in audits],
    })


@app.route("/api/branches", methods=["GET", "POST"])
@login_required
def branches_api():
    user = current_user()
    if request.method == "GET":
        with db() as c:
            if user["role"] == "branch" and user.get("branch_id"):
                rows = c.execute("SELECT * FROM branches WHERE id=?", (user["branch_id"],)).fetchall()
            else:
                rows = c.execute("SELECT * FROM branches ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])
    if user["role"] != "manager":
        return jsonify({"error": "manager access required"}), 403
    data = request.get_json(silent=True) or {}
    name_ar = str(data.get("name_ar", "")).strip()
    name_en = str(data.get("name_en", "")).strip()
    region = str(data.get("region", "")).strip()
    if not name_ar or not name_en or not region:
        return jsonify({"error": "name_ar, name_en and region are required"}), 400
    with db() as c:
        cur = c.execute("INSERT INTO branches(name_ar,name_en,region) VALUES(?,?,?)", (name_ar, name_en, region))
        row = c.execute("SELECT * FROM branches WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/audits", methods=["GET", "POST"])
@login_required
def audits_api():
    user = current_user()
    if request.method == "GET":
        with db() as c:
            params = []
            where = ""
            if user["role"] == "branch" and user.get("branch_id"):
                where = " WHERE a.branch_id=?"
                params.append(user["branch_id"])
            rows = c.execute("SELECT a.*,b.name_ar,b.name_en FROM audits a JOIN branches b ON b.id=a.branch_id" + where + " ORDER BY a.id DESC", params).fetchall()
        return jsonify([dict(r) for r in rows])
    data = request.get_json(silent=True) or {}
    try:
        branch_id = int(data.get("branch_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "valid branch_id is required"}), 400
    if user["role"] == "branch" and branch_id != user.get("branch_id"):
        return jsonify({"error": "branch access denied"}), 403
    notes = str(data.get("notes", "")).strip()
    with db() as c:
        branch = c.execute("SELECT id FROM branches WHERE id=? AND active=1", (branch_id,)).fetchone()
        if not branch:
            return jsonify({"error": "branch not found"}), 404
        cur = c.execute("INSERT INTO audits(branch_id,auditor_email,notes) VALUES(?,?,?)", (branch_id, user["email"], notes))
        row = c.execute("SELECT * FROM audits WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
