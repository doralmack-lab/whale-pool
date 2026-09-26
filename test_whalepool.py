#!/usr/bin/env python3
"""Whale Pool end-to-end tests: $30/mo membership, loan + 50% reserve,
100-backer funding, 1% ownership cap, buyback/dividend, deposit reclaim."""
import os, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DATABASE"] = os.path.join(tmp, "test.db")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_EMAIL"] = "admin@t.test"
os.environ["ADMIN_PASSWORD"] = "Admin1234"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as W

W.app.config["TESTING"] = True
client = W.app.test_client()

passed, failed = [], []
def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))

def register(name, email, pw="password123", ref=None):
    body = {"name": name, "email": email, "password": pw}
    if ref: body["referral_code"] = ref
    r = client.post("/api/auth/register", json=body)
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    return r.get_json()

def login(email, pw="password123"):
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200
    return r.get_json()["token"]

def auth_post(token, url, body=None):
    return client.post(url, json=body or {}, headers={"Authorization": f"Bearer {token}"})

def auth_get(token, url):
    return client.get(url, headers={"Authorization": f"Bearer {token}"})

with W.app.app_context():
    W.init_db()

# --- 1. membership is $30/month, 30-day period ---
u = register("Founder", "founder@t.test")
tok = u["token"]
r = auth_post(tok, "/api/billing/test-pay")
check("test-pay works", r.status_code == 200, r.status_code)
r = auth_get(tok, "/api/billing/status")
d = r.get_json()
check("monthly price is 3000c", d["monthly_price_cents"] == 3000, d.get("monthly_price_cents"))
check("membership active", d["membership_status"] == "active")
with W.app.app_context():
    db = W.get_db()
    fid = db.execute("SELECT id FROM users WHERE email='founder@t.test'").fetchone()["id"]
    pay = db.execute("SELECT * FROM payments WHERE user_id=?", (fid,)).fetchone()
    check("payment recorded at $30", pay["amount_cents"] == 3000, pay["amount_cents"])
    from datetime import datetime, timezone
    days = (datetime.fromisoformat(pay["period_end"]) - datetime.fromisoformat(pay["period_start"])).days
    check("membership period is 30 days", days == 30, days)

# --- 2. referrals: $10 credit each, 10 unlock submission ---
founder_code = u["user"]["referral_code"]
ref_toks = []
for i in range(10):
    ru = register(f"Ref{i}", f"ref{i}@t.test", ref=founder_code)
    rt = ru["token"]; ref_toks.append(rt)
    rr = auth_post(rt, "/api/billing/test-pay")
    assert rr.status_code == 200
r = auth_get(tok, "/api/me")
me = r.get_json()
check("10 referrals = $100 credits", me["referral_credits_cents"] == 10000, me["referral_credits_cents"])
check("eligible to submit", me["eligibility"]["can_submit"] is True)

# --- 3. submit project (max ask $10,000), admin approves ---
r = auth_post(tok, "/api/projects", {"title": "TestCo", "tagline": "t", "description": "d",
                                     "category": "tech", "goal_dollars": 10000})
check("project submitted", r.status_code == 201, r.status_code)
pid = r.get_json()["id"]
admin_tok = login("admin@t.test", "Admin1234")
r = client.post(f"/api/admin/projects/{pid}/decision", json={"decision": "approve"},
                headers={"Authorization": f"Bearer {admin_tok}"})
check("admin approved", r.status_code == 200, r.status_code)

# --- 4. fund with 100 backers x $100 = $10,000 ---
for i, rt in enumerate(ref_toks):
    rr = auth_post(rt, f"/api/projects/{pid}/test-pledge", {"amount_dollars": 100})
    assert rr.status_code == 200, rr.get_data(as_text=True)[:200]
# 10 backers so far; need 90 more
for i in range(10, 100):
    bu = register(f"B{i}", f"b{i}@t.test")
    bt = bu["token"]
    auth_post(bt, "/api/billing/test-pay")
    rr = auth_post(bt, f"/api/projects/{pid}/test-pledge", {"amount_dollars": 100})
    assert rr.status_code == 200
r = client.get(f"/api/projects/{pid}")
p = r.get_json()
check("project funded", p["status"] == "funded", p["status"])
check("100 funded backers", p["funded_backer_count"] == 100, p["funded_backer_count"])
check("reserve is 50% ($5,000)", p["reserve_cents"] == 500000, p["reserve_cents"])
check("disbursed is 50% ($5,000)", p["disbursed_cents"] == 500000, p["disbursed_cents"])

# --- 5. equity: 1% each ---
r = auth_get(tok, f"/api/projects/{pid}/equity")
e = r.get_json()
check("share is exactly 1%", e["share_pct_each"] == 1.0, e["share_pct_each"])
check("no backer over 1%", all(b["share_pct"] <= 1.0 for b in e["backers"]))
check("buyback = cost + 10%", all(b["buyback_cents"] == b["pledged_cents"] * 110 // 100 for b in e["backers"]))

# --- 6. reserve view ---
r = auth_get(tok, f"/api/projects/{pid}/reserve")
v = r.get_json()
check("reserve view ok", r.status_code == 200 and v["reserve_cents"] == 500000)
check("not reclaimable before buyback", v["reclaimable"] is False)
r = auth_post(tok, f"/api/projects/{pid}/reclaim-deposit")
check("reclaim blocked before buyback", r.status_code == 400, r.status_code)
# non-founder blocked
r = auth_post(ref_toks[0], f"/api/projects/{pid}/reclaim-deposit")
check("non-founder cannot reclaim", r.status_code == 403, r.status_code)

# --- 7. full buyback, then reclaim ---
r = auth_post(tok, f"/api/projects/{pid}/buyback")
check("buyback ok", r.status_code == 200, r.status_code)
check("buyback total = $11,000", r.get_json()["total_payout_cents"] == 1100000,
      r.get_json().get("total_payout_cents"))
r = auth_get(tok, f"/api/projects/{pid}/reserve")
check("reclaimable after buyback", r.get_json()["reclaimable"] is True)
r = auth_post(tok, f"/api/projects/{pid}/reclaim-deposit")
d = r.get_json()
check("reclaim ok", r.status_code == 200 and d["ok"], r.status_code)
check("reclaim releases $5,000 + yield", d["released_cents"] >= 500000, d.get("released_cents"))
r = auth_post(tok, f"/api/projects/{pid}/reclaim-deposit")
check("double reclaim blocked", r.status_code == 400, r.status_code)
with W.app.app_context():
    db = W.get_db()
    evts = [x["event_type"] for x in db.execute(
        "SELECT event_type FROM reserve_ledger WHERE project_id=? ORDER BY id", (pid,))]
    check("reserve ledger has deposit+release", evts[0] == "deposit" and evts[-1] == "release", evts)

# --- 8. dividend path on a second project (sanity) ---
r = auth_post(tok, "/api/projects", {"title": "TestCo2", "tagline": "t", "description": "d",
                                     "category": "tech", "goal_dollars": 5000})
pid2 = r.get_json()["id"]
client.post(f"/api/admin/projects/{pid2}/decision", json={"decision": "approve"},
            headers={"Authorization": f"Bearer {admin_tok}"})
for i in range(100):
    cu = register(f"C{i}", f"c{i}@t.test"); ct = cu["token"]
    auth_post(ct, "/api/billing/test-pay")
    auth_post(ct, f"/api/projects/{pid2}/test-pledge", {"amount_dollars": 50})
r = auth_post(tok, f"/api/projects/{pid2}/dividend")
check("dividend ok", r.status_code == 200, r.status_code)
check("dividend = 10% of $5,000", r.get_json()["total_payout_cents"] == 50000,
      r.get_json().get("total_payout_cents"))
r = auth_post(tok, f"/api/projects/{pid2}/dividend")
check("dividend blocked within 12mo", r.status_code == 400, r.status_code)

# --- 9. old-price copy gone ---
import re
js = open("static/app.js").read()
check("no $25 membership copy left", "$25" not in js.replace("$250", ""))

# --- 10. change password ---
r = auth_post(tok, "/api/me/password", {"current_password": "password123", "new_password": "NewPass123"})
check("change password ok", r.status_code == 200, r.status_code)
tok = login("founder@t.test", "NewPass123")
check("login works with new password", True)
r = client.post("/api/auth/login", json={"email": "founder@t.test", "password": "password123"})
check("old password rejected", r.status_code == 401, r.status_code)
r = auth_post(tok, "/api/me/password", {"current_password": "wrong", "new_password": "Another123"})
check("wrong current password -> 403", r.status_code == 403, r.status_code)
r = auth_post(tok, "/api/me/password", {"current_password": "NewPass123", "new_password": "short"})
check("short new password -> 400", r.status_code == 400, r.status_code)
r = client.post("/api/me/password", json={"current_password": "x", "new_password": "LongEnough123"})
check("change password unauthenticated -> 401", r.status_code == 401, r.status_code)

# --- 11. admin role management ---
ru = register("Roley", "roley@t.test"); rt = ru["token"]; rid = ru["user"]["id"]
r = auth_post(admin_tok, f"/api/admin/users/{rid}/role", {"is_admin": True})
check("admin can promote member", r.status_code == 200 and r.get_json()["is_admin"] is True, r.status_code)
r = auth_get(rt, "/api/me")
check("promoted user reports is_admin", r.get_json()["is_admin"] is True)
r = auth_post(ref_toks[1], f"/api/admin/users/{rid}/role", {"is_admin": False})
check("non-admin cannot change roles", r.status_code == 403, r.status_code)
r = auth_post(admin_tok, f"/api/admin/users/{rid}/role", {"is_admin": False})
check("admin can demote member", r.status_code == 200 and r.get_json()["is_admin"] is False, r.status_code)
with W.app.app_context():
    db = W.get_db()
    admin_id = db.execute("SELECT id FROM users WHERE email='admin@t.test'").fetchone()["id"]
r = auth_post(admin_tok, f"/api/admin/users/{admin_id}/role", {"is_admin": False})
check("admin cannot demote self", r.status_code == 400, r.status_code)
r = auth_post(admin_tok, "/api/admin/users/999999/role", {"is_admin": True})
check("role change on missing user -> 404", r.status_code == 404, r.status_code)

# --- 12. seed_admin upserts when the env password changes ---
W.ADMIN_PASSWORD = "ChangedAdmin99"
with W.app.app_context():
    W.seed_admin()
r = client.post("/api/auth/login", json={"email": "admin@t.test", "password": "ChangedAdmin99"})
check("admin login works after env password change + reseed", r.status_code == 200, r.status_code)
r = client.post("/api/auth/login", json={"email": "admin@t.test", "password": "Admin1234"})
check("old admin password rejected after reseed", r.status_code == 401, r.status_code)

# --- 13. referral link lands on the main page, code carried through ---
r = auth_get(tok, "/api/me")
me = r.get_json()
check("referral link points at landing page", "/#/?ref=" in me["referral_link"], me["referral_link"])
check("referral link carries own code", me["referral_link"].endswith("?ref=" + me["referral_code"]), me["referral_link"])
js = open("static/app.js").read()
check("landing join buttons carry ref", 'href="${joinHref}"' in js)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
