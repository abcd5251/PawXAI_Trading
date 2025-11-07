# ingest_api.py
import json 
import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
import aiohttp
import uvicorn
import httpx
from processor import analyze_description
from bot import (
    notify_ingest_analysis,
    notify_ingest_source,
    notify_ingest_source_async,
    notify_ingest_analysis_async,
)
from listener import MessageListener


# Use FastAPI lifespan to manage startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize shared state
    app.state.listener_client = None
    app.state.listener_task = None
    # Track last processed tweet id per screen_name to avoid re-analyzing
    app.state.last_tweet_ids = {}

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("[lifespan] DISCORD_TOKEN not set. Discord listener will not start.")
    else:
        client = MessageListener()
        app.state.listener_client = client
        # Run the discord client concurrently within the same event loop
        app.state.listener_task = asyncio.create_task(client.start(token))
        print("[lifespan] Discord listener started.")

    # Start Twitter WS worker
    app.state.twitter_ws_task = asyncio.create_task(twitter_ws_worker(app))
    print("[lifespan] Twitter WS listener started.")

    # Yield control to run the app
    try:
        yield
    finally:
        # Cleanly shutdown resources
        client = getattr(app.state, "listener_client", None)
        task = getattr(app.state, "listener_task", None)
        twitter_task = getattr(app.state, "twitter_ws_task", None)
        if client:
            try:
                await client.close()
            except Exception as e:
                print(f"[lifespan] Error closing listener client: {e}")
        if task:
            try:
                await asyncio.wait_for(task, timeout=5)
            except Exception:
                # Task may already be done or cancelled; ignore
                pass
        if twitter_task:
            try:
                twitter_task.cancel()
                await asyncio.wait_for(twitter_task, timeout=5)
            except Exception:
                pass
        print("[lifespan] Discord listener stopped.")


app = FastAPI(lifespan=lifespan)


def _norm(value):
    if value is None:
        return None
    return value.strip().strip("`").strip()


# --- Shared Twitter ingest logic (used by API and WS worker) ---
async def _process_twitter_payload(app: FastAPI, payload: dict):
    d = payload.get("data") or {}
    twitter_user = d.get("twitterUser") or {}
    status = d.get("status") or {}
    payload_type = payload.get("type")

    screen_name = twitter_user.get("screenName")
    author_name = twitter_user.get("name") or screen_name
    tweet_id = status.get("id")
    text = status.get("text")
    updated_at = status.get("updatedAt")
    changes = d.get("changes") or {}
    last_tweet_change = (changes.get("lastTweetId") or {}).get("new")

    author_url = f"https://x.com/{screen_name}" if screen_name else None
    tweet_url = (
        f"https://x.com/{screen_name}/status/{tweet_id}"
        if screen_name and tweet_id
        else None
    )

    filtered = None
    text_to_analyze = None

    # Gate: only analyze newly captured tweets
    # - Prefer explicit change signal when present
    # - Fallback to in-memory dedup per screen_name
    last_seen = None
    try:
        last_seen = (app.state.last_tweet_ids or {}).get(screen_name)
    except Exception:
        last_seen = None

    is_new_by_change = (
        tweet_id is not None and last_tweet_change is not None and str(last_tweet_change) == str(tweet_id)
    )
    is_new_by_memory = (
        tweet_id is not None and (last_seen is None or str(last_seen) != str(tweet_id))
    )
    # Accept only when there is text and either explicit change or unseen by memory
    should_analyze = bool(text and str(text).strip() and (is_new_by_change or is_new_by_memory))

    if should_analyze:
        filtered = {
            "author": {
                "name": author_name,
                "url": _norm(author_url),
            },
            "timestamp": updated_at,
            "description": text,
            "url": _norm(tweet_url),
            "title": None,
        }
        text_to_analyze = text
        # Update last seen id
        if screen_name and tweet_id is not None:
            try:
                app.state.last_tweet_ids[screen_name] = tweet_id
            except Exception:
                pass

    if text_to_analyze:
        # 1) Send source notification immediately via event-loop task
        asyncio.create_task(notify_ingest_source_async(filtered))

        # 2) Offload analysis to thread executor to keep loop responsive
        loop = asyncio.get_running_loop()
        analysis = await loop.run_in_executor(None, analyze_description, text_to_analyze)
        payload_out = {"source": filtered, "analysis": analysis}
        print(json.dumps(payload_out, ensure_ascii=False, indent=2))
        # Schedule analysis notification asynchronously
        asyncio.create_task(notify_ingest_analysis_async(analysis, filtered))

        # 3) Return HTTP-like response dict
        return {"ok": True, "data": analysis, "source": filtered}

    return {"ok": True, "data": None}


# --- Background WebSocket worker to auto-ingest tweets ---
async def twitter_ws_worker(app: FastAPI):
    url = os.getenv(
        "WSS_URL",
        "wss://p01--foxhole-backend--jb924j8sn9fb.code.run/ws/ethHackathonsrezIXgjXNr7ukySN6qNY",
    )
    usernames_env = os.getenv("TWITTER_USERNAMES", "gogo_allen15,dynavest_ai")
    usernames = [u.strip() for u in usernames_env.split(",") if u.strip()]

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.ws_connect(url, heartbeat=30) as ws:
                    # Subscribe all usernames
                    for u in usernames:
                        await ws.send_str(
                            json.dumps({"type": "subscribe", "twitterUsername": u})
                        )
                    print(f"[twitter-ws] subscribed: {', '.join(usernames)}")

                    # Gate analysis to only start after N seconds from connection
                    loop = asyncio.get_running_loop()
                    connected_at = loop.time()
                    delay_sec = float(os.getenv("TWITTER_ANALYSIS_DELAY_SEC", "10"))
                    print(f"[twitter-ws] analysis will start after {delay_sec}s from connect")

                    try:
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                # Ignore frames until delay window has passed
                                if (loop.time() - connected_at) < delay_sec:
                                    # Optionally log or silently skip initial backlog
                                    # print("[twitter-ws] skipping initial backlog within delay window")
                                    continue
                                try:
                                    payload = json.loads(msg.data)
                                except json.JSONDecodeError:
                                    print(f"[twitter-ws] non-JSON frame: {msg.data}")
                                    continue
                                # Process payload directly (no HTTP self-call)
                                try:
                                    # Use shared ingest with gating to analyze only new tweets
                                    await _process_twitter_payload(app, payload)
                                except Exception as e:
                                    print(f"[twitter-ws] processing error: {e}")
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                break
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                break
                    finally:
                        # Unsubscribe on exit
                        for u in usernames:
                            try:
                                await ws.send_str(
                                    json.dumps({
                                        "type": "unsubscribe",
                                        "twitterUsername": u,
                                    })
                                )
                            except Exception:
                                pass
            except asyncio.CancelledError:
                # Task cancelled during shutdown
                break
            except Exception as e:
                print(f"[twitter-ws] connection error: {e}")
                await asyncio.sleep(5)


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

        # 2) Offload analysis to thread executor to keep loop responsive
        loop = asyncio.get_running_loop()
        analysis = await loop.run_in_executor(None, analyze_description, text_to_analyze)
        payload = {"source": filtered, "analysis": analysis}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        # Schedule analysis notification asynchronously
        asyncio.create_task(notify_ingest_analysis_async(analysis, filtered))

        # 3) Return HTTP with analysis and filtered source
        return {"ok": True, "data": analysis, "source": filtered}

    # No message content; do not notify
    return {"ok": True, "data": None}


@app.post("/ingest/twitter")
async def ingest_twitter(req: Request):
    payload = await req.json()
    # Delegate to shared processor which gates analysis to new tweets only
    print("payload:", payload)
    result = await _process_twitter_payload(app, payload)
    print("result:", result)
    return result


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)