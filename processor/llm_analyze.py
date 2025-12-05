import json
from typing import Any, Dict, Optional

import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from processor.extractor import extract_ticker

def _utc8_now_str() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M (UTC+8)")


def _visual_bar(percent: int) -> str:
    total = 10
    filled = max(0, min(total, round(total * (percent / 100))))
    return "▰" * filled + "▱" * (total - filled)


def _run_run_all() -> Dict[str, Any]:
    """Execute project-level run_all.sh and return its result dict."""
    try:
        root = Path(__file__).resolve().parent.parent
        script = root / "run_all.sh"
        if not script.exists():
            return {"ok": False, "error": f"script not found: {script}"}
        # Use bash to avoid executable permission issues
        proc = subprocess.run([
            "/bin/bash",
            str(script),
        ], capture_output=True, text=True)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def analyze_description(description: Optional[str], temperature: float = 0.0) -> Dict[str, Any]:
    """
    Extract tickers from a text and build a Telegram-friendly signal message.

    Returns a dict: {"has_ticker": bool, "ticker": list[str], "telegram_text": str|None}
    - When has_ticker is True, telegram_text contains a formatted signal message.
    - Otherwise, telegram_text is None.
    """

    if not description or not description.strip():
        return {"has_ticker": False, "ticker": [], "telegram_text": None}

    try:
        result = extract_ticker(description)
        has_ticker = bool(result.get("has_ticker"))
        tickers = result.get("ticker") or []

        if not has_ticker:
            return {"has_ticker": False, "ticker": [], "telegram_text": None}

        symbol = str(tickers[0]).upper()
        operate = "LONG"
        leverage = 5
        position_percent = 70
        visual = _visual_bar(position_percent)
        now_str = _utc8_now_str()

        telegram_text = (
            f"📡 Live Trading Signal — <b>{symbol}</b>\n"
            f"Direction: 🔺 <b>{operate}</b>\n"
            f"Position Size: <b>{position_percent}%</b>\n"
            f"Visual: {visual}\n"
            f"Leverage: <b>{leverage}x</b>\n"
            f"Source: Tier1 Tweet Alert\n"
            f"Time: {now_str}\n\n"
        )
        # Execute run_all.sh before returning result
        script_result = _run_run_all()

        return {
            "has_ticker": True,
            "ticker": tickers,
            "telegram_text": telegram_text
        }
    except Exception as e:
        return {"has_ticker": False, "ticker": [], "telegram_text": None, "error": str(e)}


# from models.gemini_model import GeminiModel
# from prompts.extractor import extractor_prompt


# def build_prompt(description: str) -> str:
#     """Wrap the description as a TWITTER_POST for the extractor prompt."""
#     return f"TWITTER_POST:\n{description.strip()}"


# def analyze_description(description: Optional[str], temperature: float = 0.0) -> Dict[str, Any]:
#     """
#     Analyze a tweet description using the extractor prompt and return a JSON dict.

#     If the model fails or description is empty, return a conservative fallback.
#     """
#     fallback = {
#         "symbol": "UNKNOWN",
#         "operate": "long",
#         "leverage": 5,
#         "confidence": 0.1,
#     }
#     print("analyze start !!")

#     if not description or not description.strip():
#         return fallback

#     try:
#         #model = OpenAIModel(system_prompt=extractor_prompt, temperature=temperature)
#         #prompt = build_prompt(description)
#         #response_text, _, _ = model.generate_text(prompt)
#         response_text = {
#             "symbol": "Testing",      # Target token symbol, or "UNKNOWN" if unclear
#             "operate": "long",      # "long" or "short" based on sentiment
#             "leverage": 5,          # Integer between 5 and 30
#             "confidence": 0.9       # Float between 0 and 1
#         }   
#         # The model is configured with response_format json_object, but be defensive.
#         if isinstance(response_text, dict):
#             return response_text

#         return json.loads(response_text)
#     except Exception as e:
#         # Return fallback with error detail for observability
#         return {**fallback, "error": str(e)}