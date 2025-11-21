# ingest_api.py
import json
import os
import asyncio
from contextlib import asynccontextmanager

from utils.constants import KOL_LIST

from fastapi import FastAPI
import aiohttp
import uvicorn
from processor.llm_analyze import analyze_description
from bot import (
    notify_ingest_source_async,
    notify_ingest_analysis_async,
)


# Use FastAPI lifespan to manage startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Track last processed tweet id per screen_name to avoid re-analyzing
    app.state.last_tweet_ids = {}

    # Start Twitter WS worker only (Discord removed)
    app.state.twitter_ws_task = asyncio.create_task(twitter_ws_worker(app))

    # Yield control to run the app
    try:
        yield
    finally:
        # Cleanly shutdown WS worker
        twitter_task = getattr(app.state, "twitter_ws_task", None)
        if twitter_task:
            try:
                twitter_task.cancel()
                await asyncio.wait_for(twitter_task, timeout=5)
            except Exception:
                pass


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
        # 1) Send source notification to Telegram
        source_result = await notify_ingest_source_async(filtered)

        # 2) Offload analysis to thread executor to keep loop responsive
        loop = asyncio.get_running_loop()
        analysis = await loop.run_in_executor(None, analyze_description, text_to_analyze)
        payload_out = {"source": filtered, "analysis": analysis}
        print(json.dumps(payload_out, ensure_ascii=False, indent=2))

        # 3) Send analysis notification to Telegram
        analysis_result = await notify_ingest_analysis_async(analysis, filtered)

        # Return result-like dict for observability (used by WS worker logging)
        return {"ok": True, "data": analysis, "source": filtered, "telegram": {"source": source_result, "analysis": analysis_result}}

    return {"ok": True, "data": None}


# --- Background WebSocket worker to auto-ingest tweets ---
async def twitter_ws_worker(app: FastAPI):
    url = os.getenv(
        "WSS_URL",
        "wss://p01--foxhole-backend--jb924j8sn9fb.code.run/ws/ethHackathonsrezIXgjXNr7ukySN6qNY",
    )
    usernames_env = KOL_LIST
    print(KOL_LIST)
    usernames = [u.strip() for u in usernames_env.split(",") if u.strip()]

    # Initialize ws status tracking if missing
    if not hasattr(app.state, "ws_status"):
        app.state.ws_status = {"connected": False, "last_error": None, "subscribed": []}

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.ws_connect(url, heartbeat=30) as ws:
                    app.state.ws_status["connected"] = True
                    app.state.ws_status["last_error"] = None
                    # Subscribe all usernames
                    for u in usernames:
                        await ws.send_str(
                            json.dumps({"type": "subscribe", "twitterUsername": u})
                        )
                    print(f"[twitter-ws] subscribed: {', '.join(usernames)}")
                    app.state.ws_status["subscribed"] = usernames

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
                        # Mark disconnected when leaving ws context
                        app.state.ws_status["connected"] = False
            except asyncio.CancelledError:
                # Task cancelled during shutdown
                app.state.ws_status["connected"] = False
                app.state.ws_status["last_error"] = "cancelled"
                break
            except Exception as e:
                print(f"[twitter-ws] connection error: {e}")
                app.state.ws_status["connected"] = False
                app.state.ws_status["last_error"] = str(e)
                await asyncio.sleep(5)

@app.get("/healthz")
async def healthz():
    return {"ok": True}

@app.get("/ws/status")
async def ws_status():
    status = getattr(app.state, "ws_status", {"connected": False, "last_error": None, "subscribed": []})
    return {"ok": True, "ws": status}

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
