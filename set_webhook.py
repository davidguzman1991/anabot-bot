import os
import sys
import httpx

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8463461306:AAFzLUjDwVYumh5vAR0t4572T2mj_5c4OVQ")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://web-production-c4c39.up.railway.app")
WEBHOOK_URL = f"{PUBLIC_BASE_URL.rstrip('/')}/telegram/webhook"
API_BASE = f"https://api.telegram.org/bot{TOKEN}"


def post(endpoint: str, **params):
    resp = httpx.post(f"{API_BASE}/{endpoint}", data=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get(endpoint: str, **params):
    resp = httpx.get(f"{API_BASE}/{endpoint}", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    print("== getMe")
    info = get("getMe")
    print(info)

    print("== deleteWebhook")
    deleted = post("deleteWebhook")
    print(deleted)

    print(f"== setWebhook -> {WEBHOOK_URL}")
    set_res = post("setWebhook", url=WEBHOOK_URL)
    print(set_res)

    print("== getWebhookInfo")
    webhook_info = get("getWebhookInfo")
    print(webhook_info)

    ok = all(r.get("ok") for r in (info, deleted, set_res, webhook_info))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except httpx.HTTPStatusError as exc:
        print("HTTP error:", exc)
        if exc.response is not None:
            print(exc.response.status_code, exc.response.text)
        sys.exit(1)
    except Exception as exc:
        print("Error:", exc)
        sys.exit(1)
