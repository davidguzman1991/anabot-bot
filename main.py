import os
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("anabot")

app = FastAPI(title="AnaBot", version="1.0.0")


@app.get("/")
async def root() -> PlainTextResponse:
    return PlainTextResponse("Servidor AnaBot activo")


@app.get("/health")
async def health() -> Dict[str, bool]:
    return {"ok": True}


@app.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
) -> Dict[str, bool]:
    expected_secret = os.getenv("TELEGRAM_SECRET_TOKEN")
    if expected_secret and x_telegram_bot_api_secret_token != expected_secret:
        logger.warning("Telegram webhook rechazado: secret invalido o ausente")
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        logger.exception("Telegram webhook invalido: JSON malformato")
        print("Telegram webhook: JSON malformato")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info("Telegram update recibido: %s", payload)
    print("Telegram update:", payload)

    message = payload.get("message") or payload.get("edited_message") or {}
    chat = (message.get("chat") or {}).get("id")
    text = message.get("text")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if chat and text and token:
        send_url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = {"chat_id": chat, "text": f"AnaBot recibio tu mensaje: {text}"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(send_url, json=body)
                if resp.status_code >= 400:
                    logger.error("Error enviando mensaje Telegram: %s %s", resp.status_code, resp.text)
                    print("Error enviando mensaje Telegram", resp.status_code, resp.text)
        except Exception as exc:
            logger.exception("Fallo enviando respuesta a Telegram")
            print("Excepcion Telegram", exc)
    else:
        if not token:
            logger.error("TELEGRAM_BOT_TOKEN no configurado")
        if not chat or not text:
            logger.info("Telegram update sin texto o chat valido")
            print("Telegram update sin texto o chat valido")

    return {"ok": True}


@app.get("/webhook/whatsapp")
async def whatsapp_verify(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    expected_token = os.getenv("WHATSAPP_VERIFY_TOKEN")
    if hub_mode == "subscribe" and hub_verify_token == expected_token:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("invalid", status_code=403)


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request) -> Dict[str, bool]:
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        logger.exception("WhatsApp webhook invalido: JSON malformato")
        print("WhatsApp webhook: JSON malformato")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info("WhatsApp update recibido: %s", payload)
    print("WhatsApp update:", payload)

    message = (
        payload.get("entry") or [{}]
    )[0].get("changes", [{}])[0].get("value", {}).get("messages", [{}])[0]
    text_body = (message.get("text") or {}).get("body")
    wa_from = message.get("from")

    token = os.getenv("WHATSAPP_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

    if text_body and wa_from and token and phone_id:
        send_url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {
            "messaging_product": "whatsapp",
            "to": wa_from,
            "type": "text",
            "text": {"body": f"AnaBot recibio tu mensaje: {text_body}"},
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(send_url, json=body, headers=headers)
                if resp.status_code >= 400:
                    logger.error("Error enviando mensaje WhatsApp: %s %s", resp.status_code, resp.text)
                    print("Error enviando mensaje WhatsApp", resp.status_code, resp.text)
        except Exception as exc:
            logger.exception("Fallo enviando respuesta a WhatsApp")
            print("Excepcion WhatsApp", exc)
    else:
        if not token or not phone_id:
            logger.error("Credenciales de WhatsApp incompletas")
            print("Credenciales de WhatsApp incompletas")
        if not text_body or not wa_from:
            logger.info("WhatsApp update sin texto o remitente valido")
            print("WhatsApp update sin texto o remitente valido")

    return {"ok": True}
