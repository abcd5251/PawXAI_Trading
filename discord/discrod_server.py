import json
import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
from processor.llm_analyze import analyze_description
from bot import (
    notify_ingest_source_async,
    notify_ingest_analysis_async,
)
from listener import MessageListener


# Use FastAPI lifespan to manage startup/shutdown (Discord listener only)
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.listener_client = None
    app.state.listener_task = None

    token = os.getenv("DISCORD_TOKEN")
    if token:
        client = MessageListener()
        app.state.listener_client = client
        # Run the discord client concurrently within the same event loop
        app.state.listener_task = asyncio.create_task(client.start(token))
        # print("[lifespan] Discord listener started.")
    else:
        # print("[lifespan] DISCORD_TOKEN not set. Discord listener will not start.")
        pass

    try:
        yield
    finally:
        # Cleanly shutdown resources
        client = getattr(app.state, "listener_client", None)
        task = getattr(app.state, "listener_task", None)
        if client:
            try:
                await client.close()
            except Exception:
                pass
        if task:
            try:
                await asyncio.wait_for(task, timeout=5)
            except Exception:
                pass
        # print("[lifespan] Discord listener stopped.")


app = FastAPI(lifespan=lifespan)


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
        # 1) Send source notification immediately and capture result
        source_result = await notify_ingest_source_async(filtered)

        # 2) Offload analysis to thread executor to keep loop responsive
        loop = asyncio.get_running_loop()
        analysis = await loop.run_in_executor(None, analyze_description, text_to_analyze)
        payload = {"source": filtered, "analysis": analysis}
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        # 3) Send analysis notification to Telegram and capture result
        analysis_result = await notify_ingest_analysis_async(analysis, filtered)

        # 4) Return HTTP with analysis, filtered source, and Telegram results
        return {
            "ok": True,
            "data": analysis,
            "source": filtered,
            "telegram": {
                "source": source_result,
                "analysis": analysis_result,
            },
        }

    # No message content; do not notify
    return {"ok": True, "data": None}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
