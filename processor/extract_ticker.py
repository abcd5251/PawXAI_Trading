import re
from utils.constants import TICKERS


_TOKEN_RE = re.compile(r'(?:^|[^A-Za-z0-9])(?:[$#@])?([A-Za-z0-9]+)\b')

def extract_ticker(text: str) -> dict:

    candidates = _TOKEN_RE.findall(text)
    seen = set()
    found = []
    for c in candidates:
        t = c.upper()
        if t in TICKERS and t not in seen:
            seen.add(t)
            found.append(t)
    return {"has_ticker": bool(found), "ticker": found}

if __name__ == "__main__":
    string_text = "gogo DYNA LINK really good !!"
    ticker = extract_ticker(string_text)
    print(ticker)