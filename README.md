# 🐋 Whale Pool

The community fund where members back each other's boldest ideas — an
alternative to Shark Tank where the community, not a panel of sharks,
decides what gets funded.

## How it works

| Rule | Value |
|---|---|
| Monthly membership | **$30/month** (via Stripe) |
| Referral credit | **$10** per member who joins through your link |
| Referrals tracked | Automatically, via your personal referral link |
| Project eligibility | **10 credited referrals** ($100 in credits) |
| Max funding ask | Referral credits × 100 → **up to $10,000** |
| Funding rounds | New round opens **every week** (Monday) |
| Backers to fund | **100 members minimum** — dollar goal alone isn't enough |
| Project review | Admin approves every project before it goes live |

A referral is *credited* when the referred member makes their first
membership payment — this keeps the credits honest.

## Member ownership

Every funded project is member-owned:

- Funding unlocks only when **both** the dollar goal **and 100 member
  backers** are reached. With exactly 100 backers, each member owns **1%**.
- Ownership splits **equally** among backers, regardless of pledge size —
  **no member may own more than 1%** of any member's company (hard-capped).
- The founder has two ways to reward member-owners:
  - **Buy back** every share at **cost + 10%** (a $100 pledge buys back at $110),
    recorded per backer in the project's equity ledger.
  - **Or pay a 10% annual dividend** on each pledge (a $100 pledge pays $10/year),
    enforceable at most once every 12 months.
- Buybacks and dividends are recorded in the app; actual money movement
  happens through the founder's own payment rails (bank transfer, etc.).

## Loan & reserve

Every funded project is structured as a **loan** to the founder's company:

- The maximum loan is **$10,000**.
- At funding, **50% is disbursed to the founder** and **50% is held in the
  platform's income-generating reserve** to offset future financial obligations.
- The reserve accrues a notional yield (tracked at 4% APY in the app).
- The founder **recoups the full deposit + accrued yield after buying back
  100% of member shares**, via `POST /api/projects/<id>/reclaim-deposit`.
- Every reserve movement (deposit, yield, release) is written to the project's
  reserve ledger.

## Run it locally

```bash
cd whale-pool
pip install -r requirements.txt

# Seed an admin account, then start (TEST MODE — no Stripe keys needed):
ADMIN_EMAIL=you@example.com ADMIN_PASSWORD=pick-a-strong-password python app.py
```

Open **http://localhost:5050**. In test mode you'll see a gold
"TEST MODE" badge and a **Test pay $30** button that simulates payments
instantly — no Stripe account or API keys required to click through the
whole product: signup → referral link → payment → credits → project
submission → admin approval → pledges.

## Go live with Stripe (real money)

1. Create a free account at [stripe.com](https://stripe.com) and grab your
   **secret key** from Developers → API keys.
2. Add a webhook endpoint in the Stripe dashboard pointing at
   `https://YOUR-DOMAIN/api/webhooks/stripe`, listening for
   `checkout.session.completed` and `invoice.payment_succeeded`.
3. Set the env vars and restart:

```bash
export SECRET_KEY="a-long-random-string"
export STRIPE_SECRET_KEY="sk_live_..."
export STRIPE_WEBHOOK_SECRET="whsec_..."
export BASE_URL="https://YOUR-DOMAIN"
python app.py
```

That's it — the same code paths switch from simulated to real Stripe
Checkout (monthly subscriptions + one-time pledges). There is no
"no-API-keys" mode for real charges: Stripe always requires keys, and
test keys (`sk_test_...`) let you rehearse the full money flow safely
before flipping to live keys.

## Deploying

**Render (recommended, free):** the repo ships with `render.yaml` — connect
the repo in the Render dashboard and it builds with
`pip install -r requirements.txt` and starts with
`gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`. Set `ADMIN_EMAIL` and
`ADMIN_PASSWORD` in the dashboard; `SECRET_KEY` is auto-generated. Free-tier
disks are ephemeral, so the demo SQLite database resets on restart — fine
for a demo; attach a paid disk (or set `DATABASE` to hosted Postgres) for
production.

Any other host that runs Python works too (Railway, Fly.io, a VPS).
You need: Python 3.10+, `pip install -r requirements.txt`, persistent
disk for `./data/whalepool.db` (or set `DATABASE` to a persistent path),
and the env vars above. Run with gunicorn in production:

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:$PORT app:app
```

## API overview

- `POST /api/auth/register` `{name, email, password, referral_code?}` → `{token, user}`
- `POST /api/auth/login` → `{token, user}`
- `GET /api/me` → profile + referral link + eligibility (auth)
- `POST /api/billing/checkout` → Stripe subscription Checkout URL (auth)
- `POST /api/billing/test-pay` → simulated $30 payment (test mode, auth)
- `POST /api/webhooks/stripe` → Stripe events
- `GET /api/rounds/current` → this week's live projects
- `POST /api/projects` → submit a project (auth, eligibility-gated)
- `POST /api/projects/:id/pledge` → Stripe one-time Checkout URL (auth)
- `POST /api/projects/:id/test-pledge` → simulated pledge (test mode, auth)
- `GET /api/admin/projects?status=pending_review` (admin)
- `POST /api/admin/projects/:id/decision` `{decision: approve|reject, note}` (admin)
- `GET /api/admin/users`, `GET /api/admin/stats` (admin)

## Before real money flows

Talk to a lawyer about crowdfunding/securities and money-transmission
rules in the states you operate in — pooled member funding can trigger
real regulation. This codebase is a working product prototype, not legal
or compliance advice.
