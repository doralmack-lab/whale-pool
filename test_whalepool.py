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
check("landing embeds promo video", "/static/whalepool-ad-30s.mp4" in js)
check("promo video asset exists", os.path.exists("static/whalepool-ad-30s.mp4"))
check("promo poster asset exists", os.path.exists("static/ad-poster.jpg"))


# --- 14. wise payouts (sandbox-first, simulated without credentials) ---
admin_tok2 = login("admin@t.test", "ChangedAdmin99")
r = auth_get(tok, "/api/admin/payouts")
check("payouts require admin", r.status_code == 403, r.status_code)
r = auth_get(admin_tok2, "/api/admin/payouts")
d = r.get_json()
check("admin lists payouts", r.status_code == 200 and "payouts" in d, r.status_code)
check("wise starts unconfigured/simulated", d["wise_mode"] == "simulated" and d["wise_configured"] is False, d["wise_mode"])

r = auth_post(admin_tok2, "/api/admin/payouts/quote", {"amount_cents": 0})
check("quote rejects non-positive amount", r.status_code == 400, r.status_code)
r = auth_post(admin_tok2, "/api/admin/payouts/quote", {"amount_cents": 500000, "target_currency": "EUR"})
d = r.get_json()
check("quote simulated without credentials", r.status_code == 200 and d["mode"] == "simulated", r.status_code)

with W.app.app_context():
    db = W.get_db()
    founder = db.execute("SELECT id FROM users WHERE email='founder@t.test'").fetchone()
    proj = db.execute("SELECT id FROM projects LIMIT 1").fetchone()
founder_id = founder["id"]
project_id = proj["id"] if proj else None

r = auth_post(admin_tok2, "/api/admin/payouts", {"kind": "bogus", "user_id": founder_id, "amount_cents": 100})
check("payout rejects bad kind", r.status_code == 400, r.status_code)
r = auth_post(admin_tok2, "/api/admin/payouts", {"user_id": 999999, "amount_cents": 100})
check("payout rejects unknown user", r.status_code == 404, r.status_code)

payload = {"kind": "loan_disbursement", "project_id": project_id, "user_id": founder_id,
           "amount_cents": 500000, "target_currency": "USD", "recipient_details": {}}
r = auth_post(admin_tok2, "/api/admin/payouts", payload)
d = r.get_json()
p = d.get("payout", {})
check("payout created (simulated)", r.status_code == 201 and p.get("status") == "simulated",
      (r.status_code, p.get("status")))
check("payout has idempotency key", bool(p.get("customer_transaction_id")),
      p.get("customer_transaction_id"))
tid = p.get("wise_transfer_id")
r = auth_post(admin_tok2, "/api/admin/payouts", payload)
d2 = r.get_json()
check("duplicate payout is idempotent", r.status_code == 200 and d2.get("duplicate") is True,
      r.status_code)
with W.app.app_context():
    db = W.get_db()
    n = db.execute("SELECT COUNT(*) c FROM wise_payouts").fetchone()["c"]
    check("no double ledger row", n == 1, n)

r = client.post("/api/webhooks/wise", json={"transfer_id": tid, "status": "delivered"})
check("wise webhook accepted", r.status_code == 200, r.status_code)
with W.app.app_context():
    db = W.get_db()
    st = db.execute("SELECT status FROM wise_payouts WHERE wise_transfer_id = ?", (tid,)).fetchone()["status"]
    check("webhook updated payout status", st == "delivered", st)

# wise client chain, fully mocked
calls = []
def fake_api(method, path, body=None):
    calls.append((method, path))
    if path.endswith("/quotes"):
        return {"id": "q-1", "rate": 0.92, "fee": 1.5}
    if path == "/v2/accounts":
        return {"id": 42}
    if path == "/v1/transfers":
        assert body["customerTransactionId"] == "cid-1", body
        return {"id": 777}
    if path.endswith("/payments"):
        return {"status": "COMPLETED"}
    raise AssertionError(path)
W.wise._api = fake_api
W.wise.WISE_API_TOKEN = "tok"
W.wise.WISE_PROFILE_ID = "123"
res = W.wise.run_payout("loan_disbursement", 500000, "USD", "EUR", "Founder",
                        {"iban": "DE123"}, "cid-1")
check("wise chain: quote->recipient->transfer->fund",
      [c[0] for c in calls] == ["POST", "POST", "POST", "POST"], calls)
check("wise chain order", calls[0][1].endswith("/quotes") and calls[1][1] == "/v2/accounts"
      and calls[2][1] == "/v1/transfers" and calls[3][1].endswith("/payments"), calls)
check("wise transfer id captured", res["wise_transfer_id"] == "777", res)
check("wise fee captured in cents", res["fee_cents"] == 150, res)
W.wise.WISE_API_TOKEN = ""
res = W.wise.run_payout("loan_disbursement", 500000, "USD", "USD", "Founder", {}, "cid-2")
check("unconfigured wise stays simulated", res["status"] == "simulated", res)

# --- bank transfer reconciliation (BOB statement import) ---
import io as _io

b1 = register("Bank Member One", "bank1@t.test", ref=founder_code)
b1_code = b1["user"]["referral_code"]
b2 = register("Bank Member Two", "bank2@t.test")
register("Dues Member", "dues@t.test")  # never pays -> should appear in dues list

bank_csv = f"""Posting Date,Description,Reference,Credit,Debit
2026-09-01,TRF FROM Bank Member One,{b1_code},30.00,
2026-09-02,TRF FROM Bank Member Two,,30.00,
2026-09-03,TRF FROM Someone Else,UNKNOWNREF,45.00,
2026-09-04,BANK SERVICE CHARGE,,,-5.00
"""

def bank_import(csv_text, name="stmt.csv"):
    data = {"file": (_io.BytesIO(csv_text.encode()), name)}
    return client.post("/api/admin/bank/import", data=data,
                       content_type="multipart/form-data",
                       headers={"Authorization": f"Bearer {admin_tok}"})

with W.app.app_context():
    db = W.get_db()
    founder_before = db.execute(
        "SELECT referral_credits_cents FROM users WHERE email='founder@t.test'").fetchone()["referral_credits_cents"]

r = bank_import(bank_csv)
check("bank import accepts CSV", r.status_code == 200, r.status_code)
d = r.get_json()
check("import parsed 3 credits (debit skipped)", d["parsed_credits"] == 3, d)
check("import added 3 new transactions", d["new_transactions"] == 3, d)
check("import auto-matched 1 by referral code", d["auto_matched"] == 1, d)
check("no duplicates on first import", d["duplicates_skipped"] == 0, d)

with W.app.app_context():
    db = W.get_db()
    u1 = db.execute("SELECT * FROM users WHERE email='bank1@t.test'").fetchone()
    check("auto-match activated membership", u1["membership_status"] == "active", u1["membership_status"])
    pay = db.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 1",
                     (u1["id"],)).fetchone()
    check("bank payment recorded at $30", pay["amount_cents"] == 3000, pay["amount_cents"])
    check("bank payment status is bank_transfer", pay["status"] == "bank_transfer", pay["status"])
    check("bank payment reference traces to statement",
          (pay["stripe_payment_id"] or "").startswith("BOB:"), pay["stripe_payment_id"])
    txn = db.execute("SELECT * FROM bank_transactions WHERE matched_user_id=?", (u1["id"],)).fetchone()
    check("transaction marked matched", txn["status"] == "matched" and txn["payment_id"] == pay["id"])
    founder_after = db.execute(
        "SELECT referral_credits_cents FROM users WHERE email='founder@t.test'").fetchone()["referral_credits_cents"]
    check("bank payment credits referrer $10", founder_after - founder_before == 1000,
          f"{founder_before}->{founder_after}")

# re-import is idempotent
r = bank_import(bank_csv)
d = r.get_json()
check("re-import adds nothing", d["new_transactions"] == 0 and d["duplicates_skipped"] == 3, d)

# name-only match goes to review, then manual confirm by email
r = client.get("/api/admin/bank/transactions?status=needs_review",
               headers={"Authorization": f"Bearer {admin_tok}"})
txs = r.get_json()
check("name match flagged for review", len(txs) == 1 and txs[0]["suggested_name"] == "Bank Member Two", txs)
tid = txs[0]["id"]
r = client.post(f"/api/admin/bank/transactions/{tid}/match", json={"email": "bank2@t.test"},
                headers={"Authorization": f"Bearer {admin_tok}"})
check("manual match by email works", r.status_code == 200 and r.get_json()["months_credited"] == 1,
      r.get_json())
with W.app.app_context():
    db = W.get_db()
    u2 = db.execute("SELECT * FROM users WHERE email='bank2@t.test'").fetchone()
    check("manual match activated membership", u2["membership_status"] == "active")

# non-dues amount stays pending; manual match grants multiple months
r = client.get("/api/admin/bank/transactions?status=pending",
               headers={"Authorization": f"Bearer {admin_tok}"})
pend = r.get_json()
check("unknown reference stays pending", len(pend) == 1 and pend[0]["amount_cents"] == 4500, pend)
mm = register("Multi Month", "multimonth@t.test")
with W.app.app_context():
    db = W.get_db()
    mmid = db.execute("SELECT id FROM users WHERE email='multimonth@t.test'").fetchone()["id"]
r = client.post(f"/api/admin/bank/transactions/{pend[0]['id']}/match", json={"user_id": mmid},
                headers={"Authorization": f"Bearer {admin_tok}"})
check("$45 payment grants 2 months", r.get_json()["months_credited"] == 2, r.get_json())

# dues / dunning list
r = client.get("/api/admin/bank/dues", headers={"Authorization": f"Bearer {admin_tok}"})
dues = r.get_json()
dues_emails = {x["email"] for x in dues}
check("never-paid member is on dues list", "dues@t.test" in dues_emails, dues_emails)
check("active bank payer is not on dues list",
      "bank1@t.test" not in dues_emails and "bank2@t.test" not in dues_emails, dues_emails)

# member-facing billing status exposes bank instructions
r = auth_get(b1["token"], "/api/billing/status")
bd = r.get_json()["bank_transfer"]
check("billing status has bank transfer block", bd["enabled"] is True and bd["amount_cents"] == 3000, bd)
check("bank reference is the member code", bd["reference"] == b1_code, bd["reference"])

# unusable CSV is rejected with a clear error
r = bank_import("foo,bar\n1,2\n", name="bad.csv")
check("bad CSV rejected", r.status_code == 400 and "date" in r.get_json()["error"].lower(),
      r.get_json())

js = open("static/app.js").read()
check("admin UI has payouts tab", 'data-t="payouts"' in js)
check("payouts tab hits payouts API", "/api/admin/payouts" in js)
check("admin UI has bank tab", 'data-t="bank"' in js)
check("bank tab hits import API", "/api/admin/bank/import" in js)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
