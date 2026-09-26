#!/usr/bin/env python3
"""
Whale Pool — community funding platform.

Members pay $30/month. Referring members earns $10 of referral credit per
referral. With 10 referrals ($100 in credits) a member may bring a project
for a loan of up to $10,000 (credits x 100). 50% of every funded loan is paid
to the member; the other 50% is held in the platform's income-generating
reserve and is recouped by the member after buying back 100% of member shares.
Each week, admin-approved projects go live in the weekly funding round and
members can fund them.

Run:
    pip install -r requirements.txt
    ADMIN_EMAIL=you@example.com ADMIN_PASSWORD=secret python app.py

Test mode: if STRIPE_SECRET_KEY is not set, the app runs in test mode with
simulated payments (no real charges). Set STRIPE_SECRET_KEY (+ webhook
secret) to go live.
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone, date
from functools import wraps

from flask import Flask, g, jsonify, request, send_from_directory

try:
    import jwt  # PyJWT
except ImportError:  # pragma: no cover
    jwt = None

try:
    import stripe
except ImportError:  # pragma: no cover
    stripe = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get("DATABASE", os.path.join(DATA_DIR, "whalepool.db"))

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
BASE_URL = os.environ.get("BASE_URL", "").strip().rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

TEST_MODE = not bool(STRIPE_SECRET_KEY)
if stripe is not None and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

MONTHLY_PRICE_CENTS = 3000         # $30/month membership
RESERVE_PCT = 50                   # % of a funded loan held in the platform reserve
RESERVE_APY = 0.04                 # notional annual yield tracked on the reserve
REFERRAL_CREDIT_CENTS = 1000       # $10 per credited referral
REFERRALS_REQUIRED = 10            # referrals needed to submit a project
FUNDING_MULTIPLIER = 100           # max funding ask = referral credits x 100
MIN_BACKERS = 100                  # every project needs >= this many member backers to get funded
BUYBACK_NUMER = 110                # founder buyback pays pledged * 110 / 100 (cost + 10%)
DIVIDEND_NUMER = 10                # annual dividend pays pledged * 10 / 100 (10% of each pledge)

app = Flask(__name__, static_folder="static", static_url_path="/static")


# ---------------------------------------------------------------- db ----
def get_db():
    if "db" not in g:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            referral_code TEXT NOT NULL UNIQUE,
            referred_by INTEGER REFERENCES users(id),
            referral_credits_cents INTEGER NOT NULL DEFAULT 0,
            membership_status TEXT NOT NULL DEFAULT 'inactive',
            membership_period_end TEXT,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL REFERENCES users(id),
            referee_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
            credited INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            amount_cents INTEGER NOT NULL,
            status TEXT NOT NULL,
            stripe_payment_id TEXT,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL,
            tagline TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            goal_cents INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_review',
            admin_note TEXT,
            min_backers INTEGER NOT NULL DEFAULT 100,
            funded_backer_count INTEGER NOT NULL DEFAULT 0,
            equity_status TEXT NOT NULL DEFAULT 'none',
            last_dividend_at TEXT,
            reserve_cents INTEGER NOT NULL DEFAULT 0,
            disbursed_cents INTEGER NOT NULL DEFAULT 0,
            reserve_released INTEGER NOT NULL DEFAULT 0,
            reserve_released_at TEXT,
            funded_at TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS round_projects (
            round_id INTEGER NOT NULL REFERENCES rounds(id),
            project_id INTEGER NOT NULL UNIQUE REFERENCES projects(id),
            PRIMARY KEY (round_id, project_id)
        );
        CREATE TABLE IF NOT EXISTS pledges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            backer_id INTEGER NOT NULL REFERENCES users(id),
            amount_cents INTEGER NOT NULL,
            status TEXT NOT NULL,
            stripe_payment_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS equity_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            backer_id INTEGER NOT NULL REFERENCES users(id),
            event_type TEXT NOT NULL,
            pledge_cents INTEGER NOT NULL,
            payout_cents INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reserve_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            event_type TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    # migrate older databases: add any missing project columns
    cols = {r["name"] for r in db.execute("PRAGMA table_info(projects)")}
    for name, ddl in [
        ("min_backers", "INTEGER NOT NULL DEFAULT 100"),
        ("funded_backer_count", "INTEGER NOT NULL DEFAULT 0"),
        ("equity_status", "TEXT NOT NULL DEFAULT 'none'"),
        ("last_dividend_at", "TEXT"),
        ("reserve_cents", "INTEGER NOT NULL DEFAULT 0"),
        ("disbursed_cents", "INTEGER NOT NULL DEFAULT 0"),
        ("reserve_released", "INTEGER NOT NULL DEFAULT 0"),
        ("reserve_released_at", "TEXT"),
        ("funded_at", "TEXT"),
    ]:
        if name not in cols:
            db.execute(f"ALTER TABLE projects ADD COLUMN {name} {ddl}")
    db.commit()


def row_to_dict(row):
    return dict(row) if row is not None else None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# -------------------------------------------------------------- auth ----
def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${digest}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return hmac.compare_digest(check, digest)


def make_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "authentication required"}), 401
        try:
            payload = jwt.decode(auth[7:], SECRET_KEY, algorithms=["HS256"])
            subject = int(payload["sub"])
        except Exception:
            return jsonify({"error": "invalid or expired token"}), 401
        user = get_db().execute("SELECT * FROM users WHERE id = ?", (subject,)).fetchone()
        if user is None:
            return jsonify({"error": "user not found"}), 401
        g.current_user = user
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    @auth_required
    def wrapper(*args, **kwargs):
        if not g.current_user["is_admin"]:
            return jsonify({"error": "admin only"}), 403
        return fn(*args, **kwargs)

    return wrapper


def public_user(u):
    return {
        "id": u["id"],
        "name": u["name"],
        "email": u["email"],
        "referral_code": u["referral_code"],
        "referral_credits_cents": u["referral_credits_cents"],
        "membership_status": u["membership_status"],
        "membership_period_end": u["membership_period_end"],
        "is_admin": bool(u["is_admin"]),
        "created_at": u["created_at"],
    }


def make_referral_code(db):
    for _ in range(20):
        code = secrets.token_urlsafe(6).upper().replace("-", "").replace("_", "")[:8]
        if len(code) < 6:
            continue
        exists = db.execute("SELECT 1 FROM users WHERE referral_code = ?", (code,)).fetchone()
        if not exists:
            return code
    raise RuntimeError("could not generate referral code")


def referral_link_for(user, req):
    base = BASE_URL or req.host_url.rstrip("/")
    return f"{base}/#/?ref={user['referral_code']}"


def referral_stats(db, user_id):
    total = db.execute(
        "SELECT COUNT(*) c FROM referrals WHERE referrer_id = ?", (user_id,)
    ).fetchone()["c"]
    credited = db.execute(
        "SELECT COUNT(*) c FROM referrals WHERE referrer_id = ? AND credited = 1", (user_id,)
    ).fetchone()["c"]
    return {"total": total, "credited": credited}


def eligibility(db, user):
    stats = referral_stats(db, user["id"])
    credits = user["referral_credits_cents"]
    can_submit = (
        user["membership_status"] == "active"
        and stats["credited"] >= REFERRALS_REQUIRED
        and credits >= REFERRALS_REQUIRED * REFERRAL_CREDIT_CENTS
    )
    return {
        "referrals_credited": stats["credited"],
        "referrals_required": REFERRALS_REQUIRED,
        "referral_credits_cents": credits,
        "membership_active": user["membership_status"] == "active",
        "can_submit": can_submit,
        "max_goal_cents": credits * FUNDING_MULTIPLIER,
    }


def credit_referrer_for(db, user_id):
    """Credit $10 to the referrer the first time this user pays. Returns True if credited."""
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None or not user["referred_by"]:
        return False
    ref = db.execute(
        "SELECT * FROM referrals WHERE referee_id = ? AND referrer_id = ?",
        (user_id, user["referred_by"]),
    ).fetchone()
    if ref is None or ref["credited"]:
        return False
    db.execute("UPDATE referrals SET credited = 1 WHERE id = ?", (ref["id"],))
    db.execute(
        "UPDATE users SET referral_credits_cents = referral_credits_cents + ? WHERE id = ?",
        (REFERRAL_CREDIT_CENTS, user["referred_by"]),
    )
    db.commit()
    return True


def activate_membership(db, user_id, stripe_customer_id=None, stripe_subscription_id=None,
                        stripe_payment_id=None, simulated=False):
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    start = datetime.now(timezone.utc)
    try:
        current_end = datetime.fromisoformat(user["membership_period_end"]) if user["membership_period_end"] else None
    except ValueError:
        current_end = None
    if current_end and current_end > start:
        start = current_end
    end = start + timedelta(days=30)
    db.execute(
        """UPDATE users SET membership_status = 'active',
           membership_period_end = ?,
           stripe_customer_id = COALESCE(?, stripe_customer_id),
           stripe_subscription_id = COALESCE(?, stripe_subscription_id)
           WHERE id = ?""",
        (end.isoformat(), stripe_customer_id, stripe_subscription_id, user_id),
    )
    db.execute(
        """INSERT INTO payments (user_id, amount_cents, status, stripe_payment_id,
           period_start, period_end, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, MONTHLY_PRICE_CENTS, "simulated" if simulated else "succeeded",
         stripe_payment_id, start.isoformat(), end.isoformat(), now_iso()),
    )
    db.commit()
    return credit_referrer_for(db, user_id)


def current_round(db):
    monday = date.today() - timedelta(days=date.today().weekday())
    week_start = monday.isoformat()
    rnd = db.execute("SELECT * FROM rounds WHERE week_start = ?", (week_start,)).fetchone()
    if rnd is None:
        db.execute(
            "INSERT INTO rounds (week_start, status, created_at) VALUES (?, 'open', ?)",
            (week_start, now_iso()),
        )
        db.commit()
        rnd = db.execute("SELECT * FROM rounds WHERE week_start = ?", (week_start,)).fetchone()
    return rnd


def project_with_totals(db, project_id):
    p = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if p is None:
        return None
    d = row_to_dict(p)
    agg = db.execute(
        "SELECT COUNT(DISTINCT backer_id) c, COALESCE(SUM(amount_cents),0) s FROM pledges "
        "WHERE project_id = ? AND status IN ('succeeded','simulated')",
        (project_id,),
    ).fetchone()
    d["pledged_cents"] = agg["s"] or 0
    d["backer_count"] = agg["c"] or 0
    min_backers = d.get("min_backers") or MIN_BACKERS
    d["min_backers"] = min_backers
    d["backers_needed"] = max(0, min_backers - d["backer_count"])
    owner = db.execute("SELECT name FROM users WHERE id = ?", (p["owner_id"],)).fetchone()
    d["owner_name"] = owner["name"] if owner else "member"
    return d


def check_funded(db, project_id):
    p = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if p is None or p["status"] != "live":
        return
    agg = db.execute(
        "SELECT COALESCE(SUM(amount_cents),0) s, COUNT(DISTINCT backer_id) c FROM pledges "
        "WHERE project_id = ? AND status IN ('succeeded','simulated')",
        (project_id,),
    ).fetchone()
    total = agg["s"] or 0
    backers = agg["c"] or 0
    min_backers = p["min_backers"] or MIN_BACKERS
    # A project is funded only when the dollar goal AND the member count are met.
    # At funding time every backer becomes an equal part-owner of the project.
    # The loan is split: (100 - RESERVE_PCT)% is disbursed to the founder and
    # RESERVE_PCT% is held in the platform's income-generating reserve. The
    # founder recoups the reserve after buying back 100% of member shares.
    if total >= p["goal_cents"] and backers >= min_backers:
        reserve_cents = total * RESERVE_PCT // 100
        disbursed_cents = total - reserve_cents
        funded_at = now_iso()
        db.execute(
            """UPDATE projects SET status='funded', funded_backer_count=?, equity_status='active',
               reserve_cents=?, disbursed_cents=?, funded_at=? WHERE id = ?""",
            (backers, reserve_cents, disbursed_cents, funded_at, project_id),
        )
        db.execute(
            """INSERT INTO reserve_ledger (project_id, event_type, amount_cents, note, created_at)
               VALUES (?, 'deposit', ?, ?, ?)""",
            (project_id, reserve_cents,
             f"{RESERVE_PCT}% of funded loan held in reserve", funded_at),
        )
        db.commit()


# ------------------------------------------------------------ routes ----
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "mode": "test" if TEST_MODE else "live"})


@app.route("/api/stats")
def public_stats():
    db = get_db()
    members = db.execute("SELECT COUNT(*) c FROM users WHERE is_admin = 0").fetchone()["c"]
    active = db.execute(
        "SELECT COUNT(*) c FROM users WHERE is_admin = 0 AND membership_status = 'active'"
    ).fetchone()["c"]
    pledged = db.execute(
        "SELECT COALESCE(SUM(amount_cents),0) s FROM pledges WHERE status IN ('succeeded','simulated')"
    ).fetchone()["s"] or 0
    funded = db.execute("SELECT COUNT(*) c FROM projects WHERE status = 'funded'").fetchone()["c"]
    return jsonify({
        "members": members,
        "active_members": active,
        "total_pledged_cents": pledged,
        "projects_funded": funded,
        "mode": "test" if TEST_MODE else "live",
    })


# ---- auth ----
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    ref_code = (data.get("referral_code") or "").strip().upper()
    if not name or not email or len(password) < 8:
        return jsonify({"error": "name, email and a password of 8+ characters are required"}), 400
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        return jsonify({"error": "an account with that email already exists"}), 400
    referrer = None
    if ref_code:
        referrer = db.execute("SELECT * FROM users WHERE referral_code = ?", (ref_code,)).fetchone()
        if referrer is None:
            return jsonify({"error": "that referral code is not valid"}), 400
    code = make_referral_code(db)
    cur = db.execute(
        """INSERT INTO users (name, email, password_hash, referral_code, referred_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, email, hash_password(password), code,
         referrer["id"] if referrer else None, now_iso()),
    )
    user_id = cur.lastrowid
    if referrer:
        db.execute(
            "INSERT INTO referrals (referrer_id, referee_id, created_at) VALUES (?, ?, ?)",
            (referrer["id"], user_id, now_iso()),
        )
    db.commit()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return jsonify({"token": make_token(user_id), "user": public_user(user)})


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user is None or not verify_password(password, user["password_hash"]):
        return jsonify({"error": "invalid email or password"}), 401
    return jsonify({"token": make_token(user["id"]), "user": public_user(user)})


@app.route("/api/me")
@auth_required
def me():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.current_user["id"],)).fetchone()
    out = public_user(user)
    out["referral_link"] = referral_link_for(user, request)
    out["referral_stats"] = referral_stats(db, user["id"])
    out["eligibility"] = eligibility(db, user)
    return jsonify(out)


@app.route("/api/me/password", methods=["POST"])
@auth_required
def change_password():
    data = request.get_json(force=True, silent=True) or {}
    current = data.get("current_password") or ""
    new = data.get("new_password") or ""
    if len(new) < 8:
        return jsonify({"error": "new password must be at least 8 characters"}), 400
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.current_user["id"],)).fetchone()
    if not verify_password(current, user["password_hash"]):
        return jsonify({"error": "current password is incorrect"}), 403
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(new), user["id"]),
    )
    db.commit()
    return jsonify({"ok": True})


# ---- billing ----
@app.route("/api/billing/status")
@auth_required
def billing_status():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.current_user["id"],)).fetchone()
    return jsonify({
        "mode": "test" if TEST_MODE else "live",
        "membership_status": user["membership_status"],
        "membership_period_end": user["membership_period_end"],
        "monthly_price_cents": MONTHLY_PRICE_CENTS,
    })


@app.route("/api/billing/checkout", methods=["POST"])
@auth_required
def billing_checkout():
    if TEST_MODE or stripe is None:
        return jsonify({"error": "test_mode", "message": "Stripe is not configured; use test-pay"}), 400
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.current_user["id"],)).fetchone()
    base = BASE_URL or request.host_url.rstrip("/")
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=user["stripe_customer_id"] or None,
        line_items=[{
            "price_data": {
                "unit_amount": MONTHLY_PRICE_CENTS,
                "currency": "usd",
                "recurring": {"interval": "month"},
                "product_data": {"name": "Whale Pool Monthly Membership"},
            },
            "quantity": 1,
        }],
        metadata={"type": "membership", "user_id": str(user["id"])},
        subscription_data={"metadata": {"type": "membership", "user_id": str(user["id"])}},
        success_url=f"{base}/#/dashboard?paid=1",
        cancel_url=f"{base}/#/dashboard?canceled=1",
    )
    return jsonify({"url": session.url})


@app.route("/api/billing/test-pay", methods=["POST"])
@auth_required
def billing_test_pay():
    if not TEST_MODE:
        return jsonify({"error": "disabled outside test mode"}), 400
    credited = activate_membership(get_db(), g.current_user["id"], simulated=True)
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.current_user["id"],)).fetchone()
    return jsonify({"user": public_user(user), "referrer_credited": credited})


@app.route("/api/webhooks/stripe", methods=["POST"])
def stripe_webhook():
    if stripe is None:
        return jsonify({"error": "stripe not installed"}), 400
    payload = request.get_data(as_text=True)
    sig = request.headers.get("Stripe-Signature", "")
    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    etype = event.get("type") if isinstance(event, dict) else event.type
    obj = event.get("data", {}).get("object", {}) if isinstance(event, dict) else event.data.object
    db = get_db()

    def meta(key):
        if isinstance(obj, dict):
            return (obj.get("metadata") or {}).get(key)
        return (obj.metadata or {}).get(key)

    if etype == "checkout.session.completed":
        kind = meta("type")
        user_id = meta("user_id")
        if kind == "membership" and user_id:
            cust = obj.get("customer") if isinstance(obj, dict) else obj.customer
            sub = obj.get("subscription") if isinstance(obj, dict) else obj.subscription
            activate_membership(db, int(user_id), stripe_customer_id=cust,
                                stripe_subscription_id=sub,
                                stripe_payment_id=(obj.get("payment_intent") if isinstance(obj, dict) else obj.payment_intent))
        elif kind == "pledge" and meta("pledge_id"):
            pledge_id = int(meta("pledge_id"))
            db.execute("UPDATE pledges SET status = 'succeeded' WHERE id = ?", (pledge_id,))
            db.commit()
            pl = db.execute("SELECT project_id FROM pledges WHERE id = ?", (pledge_id,)).fetchone()
            if pl:
                check_funded(db, pl["project_id"])
    elif etype == "invoice.payment_succeeded":
        sub = obj.get("subscription") if isinstance(obj, dict) else obj.subscription
        if sub:
            user = db.execute("SELECT * FROM users WHERE stripe_subscription_id = ?", (sub,)).fetchone()
            if user:
                activate_membership(db, user["id"])
    return jsonify({"received": True})


# ---- projects ----
@app.route("/api/rounds/current")
def round_current():
    db = get_db()
    rnd = current_round(db)
    # Live projects stay visible across weeks until they are funded,
    # so the 100-member backer requirement can be reached over time.
    rows = db.execute(
        """SELECT p.id FROM projects p
           WHERE p.status IN ('live','funded')
           ORDER BY p.created_at"""
    ).fetchall()
    projects = [project_with_totals(db, r["id"]) for r in rows]
    return jsonify({"week_start": rnd["week_start"], "status": rnd["status"], "projects": projects})


@app.route("/api/projects/<int:project_id>")
def project_detail(project_id):
    db = get_db()
    p = project_with_totals(db, project_id)
    if p is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(p)


@app.route("/api/projects", methods=["POST"])
@auth_required
def project_create():
    data = request.get_json(force=True, silent=True) or {}
    title = (data.get("title") or "").strip()
    tagline = (data.get("tagline") or "").strip()
    description = (data.get("description") or "").strip()
    category = (data.get("category") or "general").strip()[:40]
    try:
        goal_cents = int(round(float(data.get("goal_dollars") or 0) * 100))
    except (TypeError, ValueError):
        return jsonify({"error": "goal must be a number"}), 400
    if not title or not tagline or not description:
        return jsonify({"error": "title, tagline and description are required"}), 400
    if goal_cents < 10000:
        return jsonify({"error": "funding goal must be at least $100"}), 400
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.current_user["id"],)).fetchone()
    elig = eligibility(db, user)
    if not elig["membership_active"]:
        return jsonify({"error": "an active $30/month membership is required to submit a project"}), 403
    if elig["referrals_credited"] < REFERRALS_REQUIRED:
        return jsonify({"error": f"you need {REFERRALS_REQUIRED} credited referrals to submit a project"}), 403
    if goal_cents > elig["max_goal_cents"]:
        return jsonify({"error": f"with your referral credits your max funding ask is ${elig['max_goal_cents']/100:,.0f}"}), 403
    cur = db.execute(
        """INSERT INTO projects (owner_id, title, tagline, description, category, goal_cents, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user["id"], title, tagline, description, category, goal_cents, now_iso()),
    )
    db.commit()
    return jsonify(project_with_totals(db, cur.lastrowid)), 201


@app.route("/api/me/projects")
@auth_required
def my_projects():
    db = get_db()
    rows = db.execute(
        "SELECT id FROM projects WHERE owner_id = ? ORDER BY created_at DESC",
        (g.current_user["id"],),
    ).fetchall()
    return jsonify([project_with_totals(db, r["id"]) for r in rows])


@app.route("/api/projects/<int:project_id>/pledge", methods=["POST"])
@auth_required
def pledge_create(project_id):
    data = request.get_json(force=True, silent=True) or {}
    try:
        amount_cents = int(round(float(data.get("amount_dollars") or 0) * 100))
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    if amount_cents < 100:
        return jsonify({"error": "minimum pledge is $1"}), 400
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.current_user["id"],)).fetchone()
    if user["membership_status"] != "active":
        return jsonify({"error": "an active membership is required to fund projects"}), 403
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if proj is None or proj["status"] not in ("live",):
        return jsonify({"error": "this project is not open for funding"}), 400
    cur = db.execute(
        """INSERT INTO pledges (project_id, backer_id, amount_cents, status, created_at)
           VALUES (?, ?, ?, 'pending', ?)""",
        (project_id, user["id"], amount_cents, now_iso()),
    )
    pledge_id = cur.lastrowid
    db.commit()
    if TEST_MODE or stripe is None:
        return jsonify({"error": "test_mode", "message": "use test-pledge in test mode",
                        "pledge_id": pledge_id}), 400
    base = BASE_URL or request.host_url.rstrip("/")
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "unit_amount": amount_cents,
                "currency": "usd",
                "product_data": {"name": f"Whale Pool pledge: {proj['title']}"},
            },
            "quantity": 1,
        }],
        metadata={"type": "pledge", "pledge_id": str(pledge_id),
                  "project_id": str(project_id), "user_id": str(user["id"])},
        success_url=f"{base}/#/project/{project_id}?pledged=1",
        cancel_url=f"{base}/#/project/{project_id}?canceled=1",
    )
    return jsonify({"url": session.url, "pledge_id": pledge_id})


@app.route("/api/projects/<int:project_id>/test-pledge", methods=["POST"])
@auth_required
def pledge_test(project_id):
    if not TEST_MODE:
        return jsonify({"error": "disabled outside test mode"}), 400
    data = request.get_json(force=True, silent=True) or {}
    try:
        amount_cents = int(round(float(data.get("amount_dollars") or 0) * 100))
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    if amount_cents < 100:
        return jsonify({"error": "minimum pledge is $1"}), 400
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.current_user["id"],)).fetchone()
    if user["membership_status"] != "active":
        return jsonify({"error": "an active membership is required to fund projects"}), 403
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if proj is None or proj["status"] != "live":
        return jsonify({"error": "this project is not open for funding"}), 400
    db.execute(
        """INSERT INTO pledges (project_id, backer_id, amount_cents, status, created_at)
           VALUES (?, ?, ?, 'simulated', ?)""",
        (project_id, user["id"], amount_cents, now_iso()),
    )
    db.commit()
    check_funded(db, project_id)
    return jsonify(project_with_totals(db, project_id))


# ---- equity: member ownership, buyback, dividends ----
def cap_table(db, project_id):
    """Equal-split ownership among a funded project's backers."""
    p = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if p is None:
        return None, [], 0.0
    rows = db.execute(
        """SELECT u.id AS backer_id, u.name AS name, SUM(pl.amount_cents) AS pledged_cents
           FROM pledges pl JOIN users u ON u.id = pl.backer_id
           WHERE pl.project_id = ? AND pl.status IN ('succeeded','simulated')
           GROUP BY u.id ORDER BY pledged_cents DESC""",
        (project_id,),
    ).fetchall()
    n = p["funded_backer_count"] or len(rows) or 1
    # Hard rule: no member may own more than 1% of any member's company.
    # Funding requires >= 100 backers, so an equal split can never exceed 1%;
    # the cap below guarantees it even if the minimum ever changes.
    share = min(round(100.0 / n, 4), 1.0)
    backers = [{
        "backer_id": r["backer_id"],
        "name": r["name"],
        "pledged_cents": r["pledged_cents"],
        "share_pct": share,
        "buyback_cents": r["pledged_cents"] * BUYBACK_NUMER // 100,
        "annual_dividend_cents": r["pledged_cents"] * DIVIDEND_NUMER // 100,
    } for r in rows]
    return p, backers, share


@app.route("/api/projects/<int:project_id>/equity")
@auth_required
def project_equity(project_id):
    db = get_db()
    p, backers, share = cap_table(db, project_id)
    if p is None:
        return jsonify({"error": "not found"}), 404
    if p["equity_status"] == "none":
        return jsonify({"error": "this project is not funded yet — no equity exists"}), 400
    return jsonify({
        "project_id": p["id"],
        "title": p["title"],
        "equity_status": p["equity_status"],
        "backer_count": p["funded_backer_count"],
        "share_pct_each": share,
        "backers": backers,
        "last_dividend_at": p["last_dividend_at"],
    })


@app.route("/api/projects/<int:project_id>/buyback", methods=["POST"])
@auth_required
def project_buyback(project_id):
    """Founder buys back every member share at cost + 10%."""
    db = get_db()
    p = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if p is None:
        return jsonify({"error": "not found"}), 404
    if p["owner_id"] != g.current_user["id"]:
        return jsonify({"error": "only the project founder can buy back shares"}), 403
    if p["equity_status"] != "active":
        return jsonify({"error": "there is no active member equity to buy back"}), 400
    _, backers, _ = cap_table(db, project_id)
    total = 0
    for b in backers:
        db.execute(
            """INSERT INTO equity_ledger (project_id, backer_id, event_type, pledge_cents, payout_cents, created_at)
               VALUES (?, ?, 'buyback', ?, ?, ?)""",
            (project_id, b["backer_id"], b["pledged_cents"], b["buyback_cents"], now_iso()),
        )
        total += b["buyback_cents"]
    db.execute("UPDATE projects SET equity_status = 'bought_back' WHERE id = ?", (project_id,))
    db.commit()
    return jsonify({"ok": True, "backers": len(backers), "total_payout_cents": total})


@app.route("/api/projects/<int:project_id>/dividend", methods=["POST"])
@auth_required
def project_dividend(project_id):
    """Founder pays the 10% annual dividend to every member-owner. Once per year."""
    db = get_db()
    p = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if p is None:
        return jsonify({"error": "not found"}), 404
    if p["owner_id"] != g.current_user["id"]:
        return jsonify({"error": "only the project founder can pay dividends"}), 403
    if p["equity_status"] != "active":
        return jsonify({"error": "there is no active member equity on this project"}), 400
    if p["last_dividend_at"]:
        try:
            last = datetime.fromisoformat(p["last_dividend_at"])
        except ValueError:
            last = None
        if last is not None and (datetime.now(timezone.utc) - last).days < 365:
            return jsonify({"error": "the annual dividend was already paid within the last 12 months"}), 400
    _, backers, _ = cap_table(db, project_id)
    total = 0
    for b in backers:
        db.execute(
            """INSERT INTO equity_ledger (project_id, backer_id, event_type, pledge_cents, payout_cents, created_at)
               VALUES (?, ?, 'dividend', ?, ?, ?)""",
            (project_id, b["backer_id"], b["pledged_cents"], b["annual_dividend_cents"], now_iso()),
        )
        total += b["annual_dividend_cents"]
    db.execute("UPDATE projects SET last_dividend_at = ? WHERE id = ?", (now_iso(), project_id))
    db.commit()
    return jsonify({"ok": True, "backers": len(backers), "total_payout_cents": total})


# ---- reserve: 50% of every funded loan is held in the platform's
# income-generating reserve and recouped by the founder after a 100% buyback ----
def reserve_summary(db, project_id):
    """Reserve balance plus notional accrued yield for a funded project."""
    p = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if p is None or p["status"] != "funded":
        return None
    reserve = p["reserve_cents"] or 0
    days = 0
    if p["funded_at"]:
        try:
            funded = datetime.fromisoformat(p["funded_at"])
            days = max(0, (datetime.now(timezone.utc) - funded).days)
        except ValueError:
            days = 0
    accrued = int(reserve * RESERVE_APY * days / 365)
    return {
        "project_id": p["id"],
        "title": p["title"],
        "loan_cents": reserve + (p["disbursed_cents"] or 0),
        "disbursed_cents": p["disbursed_cents"] or 0,
        "reserve_cents": reserve,
        "reserve_pct": RESERVE_PCT,
        "accrued_yield_cents": 0 if p["reserve_released"] else accrued,
        "days_in_reserve": days,
        "reserve_released": bool(p["reserve_released"]),
        "reserve_released_at": p["reserve_released_at"],
        "buyback_complete": p["equity_status"] == "bought_back",
        "reclaimable": p["equity_status"] == "bought_back" and not p["reserve_released"],
    }


@app.route("/api/projects/<int:project_id>/reserve")
@auth_required
def project_reserve(project_id):
    db = get_db()
    summary = reserve_summary(db, project_id)
    if summary is None:
        return jsonify({"error": "this project is not funded yet — no reserve exists"}), 400
    return jsonify(summary)


@app.route("/api/projects/<int:project_id>/reclaim-deposit", methods=["POST"])
@auth_required
def project_reclaim_deposit(project_id):
    """Founder recoups the 50% reserve deposit (+ accrued yield) after buying
    back 100% of member shares."""
    db = get_db()
    p = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if p is None:
        return jsonify({"error": "not found"}), 404
    if p["owner_id"] != g.current_user["id"]:
        return jsonify({"error": "only the project founder can reclaim the deposit"}), 403
    if p["status"] != "funded":
        return jsonify({"error": "this project is not funded yet"}), 400
    if p["equity_status"] != "bought_back":
        return jsonify({"error": "the reserve is released only after you buy back 100% of member shares"}), 400
    if p["reserve_released"]:
        return jsonify({"error": "the reserve deposit was already reclaimed"}), 400
    summary = reserve_summary(db, project_id)
    now = now_iso()
    if summary["accrued_yield_cents"]:
        db.execute(
            """INSERT INTO reserve_ledger (project_id, event_type, amount_cents, note, created_at)
               VALUES (?, 'yield', ?, ?, ?)""",
            (project_id, summary["accrued_yield_cents"],
             f"notional yield at {RESERVE_APY:.0%} APY over {summary['days_in_reserve']} days", now),
        )
    total = summary["reserve_cents"] + summary["accrued_yield_cents"]
    db.execute(
        """INSERT INTO reserve_ledger (project_id, event_type, amount_cents, note, created_at)
           VALUES (?, 'release', ?, ?, ?)""",
        (project_id, total, "reserve deposit recouped after 100% share buyback", now),
    )
    db.execute(
        "UPDATE projects SET reserve_released = 1, reserve_released_at = ? WHERE id = ?",
        (now, project_id),
    )
    db.commit()
    return jsonify({"ok": True, "released_cents": total,
                    "reserve_cents": summary["reserve_cents"],
                    "yield_cents": summary["accrued_yield_cents"]})


# ---- admin ----
@app.route("/api/admin/projects")
@admin_required
def admin_projects():
    db = get_db()
    status = request.args.get("status", "pending_review")
    rows = db.execute(
        "SELECT id FROM projects WHERE status = ? ORDER BY created_at DESC", (status,)
    ).fetchall()
    return jsonify([project_with_totals(db, r["id"]) for r in rows])


@app.route("/api/admin/projects/<int:project_id>/decision", methods=["POST"])
@admin_required
def admin_decision(project_id):
    data = request.get_json(force=True, silent=True) or {}
    decision = (data.get("decision") or "").strip()
    note = (data.get("note") or "").strip()[:500]
    if decision not in ("approve", "reject"):
        return jsonify({"error": "decision must be approve or reject"}), 400
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if proj is None:
        return jsonify({"error": "not found"}), 404
    if decision == "approve":
        db.execute(
            "UPDATE projects SET status = 'live', admin_note = ?, decided_at = ? WHERE id = ?",
            (note, now_iso(), project_id),
        )
        rnd = current_round(db)
        db.execute(
            "INSERT OR IGNORE INTO round_projects (round_id, project_id) VALUES (?, ?)",
            (rnd["id"], project_id),
        )
    else:
        db.execute(
            "UPDATE projects SET status = 'rejected', admin_note = ?, decided_at = ? WHERE id = ?",
            (note, now_iso(), project_id),
        )
    db.commit()
    return jsonify(project_with_totals(db, project_id))


@app.route("/api/admin/users")
@admin_required
def admin_users():
    db = get_db()
    rows = db.execute(
        """SELECT u.*, (SELECT COUNT(*) FROM referrals r WHERE r.referrer_id = u.id AND r.credited = 1) AS credited_refs
           FROM users u ORDER BY u.created_at DESC LIMIT 200"""
    ).fetchall()
    out = []
    for u in rows:
        d = public_user(u)
        d["credited_referrals"] = u["credited_refs"]
        out.append(d)
    return jsonify(out)


@app.route("/api/admin/users/<int:user_id>/role", methods=["POST"])
@admin_required
def admin_set_role(user_id):
    data = request.get_json(force=True, silent=True) or {}
    make_admin = bool(data.get("is_admin"))
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        return jsonify({"error": "user not found"}), 404
    if target["id"] == g.current_user["id"] and not make_admin:
        return jsonify({"error": "you cannot remove your own admin access"}), 400
    db.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if make_admin else 0, user_id))
    db.commit()
    return jsonify({"ok": True, "is_admin": make_admin})


@app.route("/api/admin/stats")
@admin_required
def admin_stats():
    db = get_db()
    q = lambda sql, args=(): db.execute(sql, args).fetchone()
    return jsonify({
        "total_members": q("SELECT COUNT(*) c FROM users WHERE is_admin = 0")["c"],
        "active_members": q("SELECT COUNT(*) c FROM users WHERE is_admin = 0 AND membership_status='active'")["c"],
        "monthly_mrr_cents": q("SELECT COUNT(*) c FROM users WHERE is_admin = 0 AND membership_status='active'")["c"] * MONTHLY_PRICE_CENTS,
        "referral_credits_outstanding_cents": q("SELECT COALESCE(SUM(referral_credits_cents),0) s FROM users")["s"],
        "projects_pending": q("SELECT COUNT(*) c FROM projects WHERE status='pending_review'")["c"],
        "projects_live": q("SELECT COUNT(*) c FROM projects WHERE status='live'")["c"],
        "projects_funded": q("SELECT COUNT(*) c FROM projects WHERE status='funded'")["c"],
        "total_pledged_cents": q("SELECT COALESCE(SUM(amount_cents),0) s FROM pledges WHERE status IN ('succeeded','simulated')")["s"],
        "mode": "test" if TEST_MODE else "live",
    })


def seed_admin():
    if not (ADMIN_EMAIL and ADMIN_PASSWORD):
        return
    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
    if existing:
        # Upsert: keep the env-configured admin account in sync so a changed
        # ADMIN_PASSWORD (or a restored admin flag) takes effect on redeploy.
        db.execute(
            "UPDATE users SET password_hash = ?, is_admin = 1 WHERE id = ?",
            (hash_password(ADMIN_PASSWORD), existing["id"]),
        )
        db.commit()
        print(f"Synced admin user {ADMIN_EMAIL}")
        return
    db.execute(
        """INSERT INTO users (name, email, password_hash, referral_code, is_admin, created_at)
           VALUES (?, ?, ?, ?, 1, ?)""",
        ("Admin", ADMIN_EMAIL, hash_password(ADMIN_PASSWORD), make_referral_code(db), now_iso()),
    )
    db.commit()
    print(f"Seeded admin user {ADMIN_EMAIL}")


with app.app_context():
    init_db()
    seed_admin()

if __name__ == "__main__":
    if jwt is None:
        raise SystemExit("PyJWT is required: pip install pyjwt")
    port = int(os.environ.get("PORT", "5050"))
    print(f"Whale Pool running in {'TEST' if TEST_MODE else 'LIVE'} mode on http://localhost:{port}")
    if SECRET_KEY == "dev-secret-change-me":
        print("WARNING: using default SECRET_KEY — set SECRET_KEY env var for anything real.")
    app.run(host="0.0.0.0", port=port, debug=False)
