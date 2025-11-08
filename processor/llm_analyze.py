import json
from typing import Any, Dict, Optional

from models.model import OpenAIModel
from prompts.extractor import extractor_prompt


def build_prompt(description: str) -> str:
    """Wrap the description as a TWITTER_POST for the extractor prompt."""
    return f"TWITTER_POST:\n{description.strip()}"


def analyze_description(description: Optional[str], temperature: float = 0.0) -> Dict[str, Any]:
    """
    Analyze a tweet description using the extractor prompt and return a JSON dict.

    If the model fails or description is empty, return a conservative fallback.
    """
    fallback = {
        "symbol": "UNKNOWN",
        "operate": "long",
        "leverage": 5,
        "confidence": 0.1,
    }
    print("analyze start !!")

    if not description or not description.strip():
        return fallback

    try:
        #model = OpenAIModel(system_prompt=extractor_prompt, temperature=temperature)
        #prompt = build_prompt(description)
        #response_text, _, _ = model.generate_text(prompt)
        response_text = {
            "symbol": "Testing",      # Target token symbol, or "UNKNOWN" if unclear
            "operate": "long",      # "long" or "short" based on sentiment
            "leverage": 5,          # Integer between 5 and 30
            "confidence": 0.9       # Float between 0 and 1
        }   
        # The model is configured with response_format json_object, but be defensive.
        if isinstance(response_text, dict):
            return response_text

        return json.loads(response_text)
    except Exception as e:
        # Return fallback with error detail for observability
        return {**fallback, "error": str(e)}