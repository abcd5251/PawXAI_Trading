import asyncio
import json
from twscrape import API, gather
from twscrape.logger import set_log_level
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


async def main():
    set_log_level("INFO")

    api = API()

    cookies = "auth_token=your auth token; ct0=your ct0"  

    login = "your twitter username"

    # use cookie to add to pool
    await api.pool.add_account(
        login,
        "your twitter password",
        "your login gmail",
        "your email password",
        cookies=cookies,
    )

    # Target user (no @)
    target_login = "gogo_allen15"


    user = await api.user_by_login(target_login)

    tweets = await gather(api.user_tweets(user.id, limit=5))


    def format_dt(dt, tz_name="Asia/Taipei"):
        if dt is None:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S %z")

    def tweet_type(t):
        if getattr(t, "retweetedTweet", None):
            return "retweet"
        if getattr(t, "quotedTweet", None):
            return "quote"
        return "tweet"

    for t in tweets:
        dt = getattr(t, "createdAt", None) or getattr(t, "date", None)
        dt_str = format_dt(dt)
        content = getattr(t, "rawContent", None) or getattr(t, "content", None) or getattr(t, "text", "")
        ttype = tweet_type(t)

        print(f"[{ttype}] {dt_str} | id={t.id}")
        print(content)
        print("-" * 80)

    out_file = "user_tweets_allen.json"
    serializable = [json.loads(t.json()) for t in tweets]
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"Saved to {out_file}")

if __name__ == "__main__":
    asyncio.run(main())