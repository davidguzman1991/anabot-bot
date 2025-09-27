import json
import os

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI()


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/", response_class=PlainTextResponse)
async def root():
    return "Servidor AnaBot activo"


TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_SECRET = os.getenv("TELEGRAM_SECRET_TOKEN", "")


async def tg_send(chat_id: int, text: str):
    if not TG_TOKEN:
        print("WARN: TELEGRAM_BOT_TOKEN vacio")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text})
            try:
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                print("TG send error:", exc, "resp:", resp.text)
    except Exception as exc:
        print("TG send unexpected error:", exc)


@app.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
):
    if TG_SECRET and x_telegram_bot_api_secret_token != TG_SECRET:
        raise HTTPException(status_code=401, detail="Bad secret token")

    update = await request.json()
    print("TG update:", json.dumps(update, ensure_ascii=False))

    message = update.get("message") or update.get("edited_message")
    if message and "text" in message:
        chat_id = message["chat"]["id"]
        text = message["text"]
        await tg_send(chat_id, f"AnaBot recibio: {text}")
    return JSONResponse({"ok": True})


WA_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WA_PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WA_VERIFY = os.getenv("WHATSAPP_VERIFY_TOKEN", "verify_me")


async def wa_send(to: str, text: str):
    if not (WA_TOKEN and WA_PHONE_ID):
        print("WARN: WA_TOKEN/WA_PHONE_ID vacios")
        return
    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=headers, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                print("WA send error:", exc, "resp:", resp.text)
    except Exception as exc:
        print("WA send unexpected error:", exc)


@app.get("/webhook/whatsapp")
async def wa_verify(
    mode: str | None = None,
    challenge: str | None = None,
    token: str | None = None,
):
    if mode == "subscribe" and token == WA_VERIFY:
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook/whatsapp")
async def wa_webhook(request: Request):
    body = await request.json()
    print("WA update:", json.dumps(body, ensure_ascii=False))
    try:
        message = body["entry"][0]["changes"][0]["value"]["messages"][0]
        from_number = message["from"]
        text = message.get("text", {}).get("body", "")
        await wa_send(from_number, f"AnaBot recibio: {text}")
    except Exception as exc:
        print("WA parse/send error:", exc)
    return JSONResponse({"ok": True})
