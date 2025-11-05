import os
import json
import requests
from typing import Dict, Any, Optional
from dotenv import load_dotenv
import httpx

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Reused async HTTP client for faster Telegram sends (keep-alive)
_async_client: Optional[httpx.AsyncClient] = None


def _require_env() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in environment")
    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_CHAT_ID in environment")


def build_message(analysis: Optional[Dict[str, Any]], source: Optional[Dict[str, Any]]) -> Optional[str]:
    """Format a human-friendly message for Telegram from analysis + source."""
    # If analysis is missing, skip building a message
    if not analysis:
        return None
    lines = []
    symbol = analysis.get("symbol", "UNKNOWN")
    operate = (analysis.get("operate") or "").upper() or "?"
    leverage = analysis.get("leverage", "-")
    confidence = analysis.get("confidence", "-")
    lines.append(f"Signal: {operate} {symbol}")
    lines.append(f"Leverage: {leverage}")
    lines.append(f"Confidence: {confidence}")

    if source:
        author = source.get("author") or {}
        name = author.get("name") or "Unknown"
        lines.append(f"Author: {name}")
        if source.get("title"):
            lines.append(f"Title: {source['title']}")
        if source.get("url"):
            lines.append(f"URL: {source['url']}")
        if source.get("timestamp"):
            lines.append(f"Timestamp: {source['timestamp']}")

    return "\n".join(lines)


def send_telegram_message(text: str, chat_id: Optional[str] = None) -> Dict[str, Any]:
    """Send a message to Telegram using Bot API (synchronous)."""
    _require_env()
    chat = chat_id or TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return {"ok": True, "result": resp.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _get_async_client() -> httpx.AsyncClient:
    """Create or return a shared AsyncClient with connection pooling."""
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=100),
        )
    return _async_client


async def send_telegram_message_async(text: str, chat_id: Optional[str] = None) -> Dict[str, Any]:
    """Send a message to Telegram using Bot API (asynchronous with keep-alive)."""
    _require_env()
    chat = chat_id or TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        client = await _get_async_client()
        resp = await client.post(url, json=payload)
        return {"ok": True, "result": resp.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def build_source_message(source: Optional[Dict[str, Any]]) -> Optional[str]:
    """Build a notification message from the source payload."""
    if not source:
        return None
    lines = []
    author = source.get("author") or {}
    name = author.get("name") or "Unknown"
    lines.append(f"New message from: {name}")
    if source.get("title"):
        lines.append(f"Title: {source['title']}")
    if source.get("description"):
        # Trim overly long descriptions
        desc = str(source["description"]).strip()
        if len(desc) > 500:
            desc = desc[:497] + "..."
        lines.append(f"Text: {desc}")
    if source.get("url"):
        lines.append(f"URL: {source['url']}")
    if source.get("timestamp"):
        lines.append(f"Timestamp: {source['timestamp']}")
    return "\n".join(lines) if lines else None


def notify_ingest_source(source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Send a source-only notification to Telegram."""
    text = build_source_message(source)
    if not text:
        return {"ok": False, "error": "skipped: no source"}
    return send_telegram_message(text)


async def notify_ingest_source_async(source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Send a source-only notification to Telegram asynchronously."""
    text = build_source_message(source)
    if not text:
        return {"ok": False, "error": "skipped: no source"}
    return await send_telegram_message_async(text)


def notify_ingest_analysis(analysis: Optional[Dict[str, Any]], source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build message and send to Telegram. Suitable for FastAPI BackgroundTasks."""
    text = build_message(analysis, source)
    if not text:
        return {"ok": False, "error": "skipped: no analysis"}
    return send_telegram_message(text)


async def notify_ingest_analysis_async(analysis: Optional[Dict[str, Any]], source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build message and send to Telegram asynchronously."""
    text = build_message(analysis, source)
    if not text:
        return {"ok": False, "error": "skipped: no analysis"}
    return await send_telegram_message_async(text)


if __name__ == "__main__":
    # Simple manual test
    sample_analysis = {"symbol": "ARB", "operate": "long", "leverage": 15, "confidence": 0.78}
    sample_source = {"author": {"name": "Alice"}, "title": "Bullish on ARB", "url": "https://example.com", "timestamp": "2025-11-05T12:00:00Z"}
    print(json.dumps(notify_ingest_analysis(sample_analysis, sample_source), indent=2))