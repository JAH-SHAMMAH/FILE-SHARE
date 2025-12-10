import os
import httpx
from dotenv import load_dotenv

load_dotenv()

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")

if PAYPAL_MODE == "live":
    PAYPAL_BASE = "https://api-m.paypal.com"
else:
    PAYPAL_BASE = "https://api-m.sandbox.paypal.com"


async def get_access_token():
    auth = (PAYPAL_CLIENT_ID, PAYPAL_SECRET)
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{PAYPAL_BASE}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=auth,
        )
        r.raise_for_status()
        return r.json()["access_token"]


async def create_order(total: str, currency: str = "USD"):
    token = await get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{"amount": {"currency_code": currency, "value": total}}],
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{PAYPAL_BASE}/v2/checkout/orders", json=body, headers=headers
        )
        r.raise_for_status()
        return r.json()


async def capture_order(order_id: str):
    token = await get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{PAYPAL_BASE}/v2/checkout/orders/{order_id}/capture", headers=headers
        )
        r.raise_for_status()
        return r.json()


async def verify_webhook_signature(
    transmission_id: str,
    transmission_time: str,
    cert_url: str,
    auth_algo: str,
    transmission_sig: str,
    webhook_id: str,
    event_body: dict,
):
    """
    Verify PayPal webhook signature using PayPal REST API.
    """
    token = await get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "transmission_id": transmission_id,
        "transmission_time": transmission_time,
        "cert_url": cert_url,
        "auth_algo": auth_algo,
        "transmission_sig": transmission_sig,
        "webhook_id": webhook_id,
        "webhook_event": event_body,
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{PAYPAL_BASE}/v1/notifications/verify-webhook-signature",
            json=body,
            headers=headers,
        )
        r.raise_for_status()
        return r.json()