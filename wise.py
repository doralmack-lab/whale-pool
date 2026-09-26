"""Wise payouts client for Whale Pool (sandbox-first).

Money-in stays on Stripe. Money-out (founder loan disbursements, reserve
releases) goes through Wise, which handles 50+ currencies at the real
mid-market rate.

Sandbox-first: if WISE_API_TOKEN / WISE_PROFILE_ID are not set, every
operation runs in *simulated* mode and only touches the local ledger —
exactly like the app's Stripe test mode. Set the env vars (Render dashboard)
to talk to the real Wise sandbox at https://api.wise-sandbox.com, or
https://api.wise.com for production.

Payout chain (Wise Platform API):
    quote -> recipient -> transfer -> fund
`customer_transaction_id` is the idempotency key: reusing it returns the
original transfer instead of creating a second one, so retries are safe.
"""
import json
import os
import urllib.error
import urllib.request

WISE_API_URL = os.environ.get("WISE_API_URL", "https://api.wise-sandbox.com").rstrip("/")
WISE_API_TOKEN = os.environ.get("WISE_API_TOKEN", "").strip()
WISE_PROFILE_ID = os.environ.get("WISE_PROFILE_ID", "").strip()
WISE_WEBHOOK_SECRET = os.environ.get("WISE_WEBHOOK_SECRET", "").strip()


class WiseError(Exception):
    pass


def wise_configured():
    """True when sandbox/production credentials are present."""
    return bool(WISE_API_TOKEN and WISE_PROFILE_ID)


def wise_mode():
    if not wise_configured():
        return "simulated"
    if "sandbox" in WISE_API_URL:
        return "sandbox"
    return "live"


def _api(method, path, body=None):
    if not wise_configured():
        raise WiseError("Wise API credentials are not configured")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(WISE_API_URL + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + WISE_API_TOKEN)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:500]
        except Exception:
            detail = ""
        raise WiseError(f"Wise API {method} {path} -> HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise WiseError(f"Wise API {method} {path} unreachable: {e}")


def create_quote(source_currency, target_currency, source_amount_cents):
    """Step 1: lock a rate. Quotes are single-use and expire (~30 min)."""
    body = {
        "sourceCurrency": source_currency.upper(),
        "targetCurrency": target_currency.upper(),
        "sourceAmount": round(source_amount_cents / 100, 2),
        "payOut": "BALANCE",
    }
    return _api("POST", f"/v3/profiles/{WISE_PROFILE_ID}/quotes", body)


def create_recipient(currency, account_holder_name, details):
    """Step 2: register the founder's bank account. `details` varies by
    currency, e.g. {"iban": "..."} for EUR or
    {"sortCode": "...", "accountNumber": "..."} for GBP or
    {"routingNumber": "...", "accountNumber": "...", "accountType": "CHECKING"}
    for USD."""
    body = {
        "currency": currency.upper(),
        "type": _account_type_for(currency),
        "profile": WISE_PROFILE_ID,
        "accountHolderName": account_holder_name,
        "details": details,
    }
    # Wise v2 accounts endpoint is the current recipient API.
    return _api("POST", "/v2/accounts", body)


def _account_type_for(currency):
    c = currency.upper()
    if c == "EUR":
        return "iban"
    if c == "GBP":
        return "sort_code"
    if c == "USD":
        return "aba"
    return "iban"


def create_transfer(target_account_id, quote_uuid, customer_transaction_id,
                    reference="Whale Pool payout"):
    """Step 3: create the transfer. Idempotent on customerTransactionId."""
    body = {
        "targetAccount": target_account_id,
        "quoteUuid": quote_uuid,
        "customerTransactionId": customer_transaction_id,
        "reference": reference[:60],
        "details": {"reference": reference[:60]},
    }
    return _api("POST", "/v1/transfers", body)


def fund_transfer(transfer_id):
    """Step 4: debit the Wise balance and send. This is the point of no
    return — only call after the transfer is confirmed correct."""
    return _api(
        "POST",
        f"/v3/profiles/{WISE_PROFILE_ID}/transfers/{transfer_id}/payments",
        {"type": "BALANCE"},
    )


def get_transfer(transfer_id):
    return _api("GET", f"/v1/transfers/{transfer_id}")


def run_payout(kind, amount_cents, source_currency, target_currency,
               recipient_name, recipient_details, customer_transaction_id,
               reference="Whale Pool payout"):
    """Run the full quote -> recipient -> transfer -> fund chain.

    Returns a dict describing what happened. In simulated mode (no
    credentials) nothing leaves the machine; the ledger records a
    `simulated` payout so the whole flow can be exercised safely.
    """
    if not wise_configured():
        return {
            "mode": "simulated",
            "status": "simulated",
            "wise_transfer_id": f"sim_{customer_transaction_id[:16]}",
            "note": "WISE_API_TOKEN not set — simulated payout, no money moved",
        }
    quote = create_quote(source_currency, target_currency, amount_cents)
    quote_uuid = quote.get("id") or quote.get("uuid")
    if not quote_uuid:
        raise WiseError(f"quote did not return an id: {quote}")
    recipient = create_recipient(target_currency, recipient_name, recipient_details)
    recipient_id = recipient.get("id")
    if not recipient_id:
        raise WiseError(f"recipient did not return an id: {recipient}")
    transfer = create_transfer(recipient_id, quote_uuid,
                               customer_transaction_id, reference)
    transfer_id = transfer.get("id")
    if not transfer_id:
        raise WiseError(f"transfer did not return an id: {transfer}")
    funding = fund_transfer(transfer_id)
    return {
        "mode": wise_mode(),
        "status": funding.get("status", "created").lower(),
        "wise_quote_id": str(quote_uuid),
        "wise_recipient_id": str(recipient_id),
        "wise_transfer_id": str(transfer_id),
        "rate": quote.get("rate"),
        "fee_cents": int(round(float(quote.get("fee", 0)) * 100)),
    }
