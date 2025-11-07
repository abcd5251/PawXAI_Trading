import os
import json
import asyncio
import aiohttp


WSS_URL = os.getenv(
    "WSS_URL",
    "wss://p01--foxhole-backend--jb924j8sn9fb.code.run/ws/ethHackathonsrezIXgjXNr7ukySN6qNY",
)
# 支援一次訂閱多位使用者，環境變數以逗號分隔
TWITTER_USERNAMES = "elonmusk,gogo_allen15,dynavest_ai"
DEFAULT_USERNAME = os.getenv("TWITTER_USERNAME", "elonmusk")


async def connect_and_listen(url: str, usernames):
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, heartbeat=30) as ws:
            # 逐一送出訂閱
            for u in usernames:
                await ws.send_str(json.dumps({"type": "subscribe", "twitterUsername": u}))
            # 額外明確訂閱 gogo_allen15 與 dynavest_ai（依你的要求）
            for extra in ("gogo_allen15", "dynavest_ai"):
                if extra not in usernames:
                    await ws.send_str(json.dumps({"type":"subscribe","twitterUsername": extra}))
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        # 嘗試解析 JSON，若失敗則原樣輸出
                        try:
                            payload = json.loads(msg.data)
                            print(f"[WS] type={payload.get('type')} data={payload}")
                        except json.JSONDecodeError:
                            print(f"[WS] 收到訊息: {msg.data}")
                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        break
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break
            finally:
                try:
                    # 離線前逐一取消訂閱
                    for u in usernames:
                        await ws.send_str(
                            json.dumps({"type": "unsubscribe", "twitterUsername": u})
                        )
                    for extra in ("gogo_allen15", "dynavest_ai"):
                        if extra not in usernames:
                            await ws.send_str(json.dumps({"type":"unsubscribe","twitterUsername": extra}))
                except Exception:
                    pass


async def main():
    url = WSS_URL
    # 解析多使用者
    if TWITTER_USERNAMES:
        usernames = [u.strip() for u in TWITTER_USERNAMES.split(",") if u.strip()]
    else:
        usernames = [DEFAULT_USERNAME]
    while True:
        try:
            await connect_and_listen(url, usernames)
        except Exception as e:
            print(f"ws error: {e}")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())