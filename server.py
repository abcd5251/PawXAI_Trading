# ingest_api.py
import json 
import os
import asyncio
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
import httpx
from processor import analyze_description
from bot import (
    notify_ingest_analysis,
    notify_ingest_source,
    notify_ingest_source_async,
    notify_ingest_analysis_async,
)

app = FastAPI()

# WWW Optional: push each event to an external webhook (e.g., n8n)
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
_wh_client = None

async def _get_wh_client() -> httpx.AsyncClient:
    global _wh_client
    if _wh_client is None:
        _wh_client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=100),
        )
    return _wh_client

async def emit_webhook(event: str, payload: dict) -> None:
    if not WEBHOOK_URL:
        return
    try:
        client = await _get_wh_client()
        body = {"event": event, "payload": payload}
        await client.post(WEBHOOK_URL, json=body)
    except Exception as e:
        print(f"Webhook error: {e}")


def _norm(value):
    if value is None:
        return None
    return value.strip().strip("`").strip()


@app.post("/ingest")
async def ingest(req: Request, background_tasks: BackgroundTasks):
    data = await req.json()
    embeds = data.get("embeds") or []

    filtered = None
    analysis = None
    text_to_analyze = None

    # Prefer embed description if available
    for e in embeds:
        author = e.get("author") or {}
        name = author.get("name")
        desc = e.get("description")
        if desc and str(desc).strip():
            filtered = {
                "author": {
                    "name": name,
                    "url": _norm(author.get("url")),
                },
                "timestamp": e.get("timestamp"),
                "description": desc,
                "url": _norm(e.get("url")),
                "title": e.get("title"),
            }
            text_to_analyze = desc
            break

    # Fallback to plain message content if no usable embed description
    if not text_to_analyze:
        content = data.get("content")
        if content and str(content).strip():
            filtered = filtered or {
                "author": {"name": data.get("author_name"), "url": None},
                "timestamp": data.get("created_at"),
                "description": content,
                "url": data.get("jump_url"),
                "title": None,
            }
            text_to_analyze = content

    # Only notify and analyze when we have actual text
    if text_to_analyze:
        # 1) Send source notification immediately via event-loop task
        asyncio.create_task(notify_ingest_source_async(filtered))

        # WWW Webhook part Also emit webhook with raw source webhook part
        asyncio.create_task(emit_webhook("source", filtered))

        # 2) Offload analysis to thread executor to keep loop responsive
        loop = asyncio.get_running_loop()
        analysis = await loop.run_in_executor(None, analyze_description, text_to_analyze)
        payload = {"source": filtered, "analysis": analysis}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        # Schedule analysis notification asynchronously
        asyncio.create_task(notify_ingest_analysis_async(analysis, filtered))

        # WWW Webhook part Also emit webhook with analysis + source
        asyncio.create_task(emit_webhook("analysis", payload))

        # 3) Return HTTP with analysis and filtered source
        return {"ok": True, "data": analysis, "source": filtered}

    # No message content; do not notify
    return {"ok": True, "data": None}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)