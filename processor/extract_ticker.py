import re

_TICKERS = {
    'SEI','TRUMP','MYX','USDCAD','ICP','APT','AVNT','STRK','LINK','PAXG','GMX','1000FLOKI',
    'EDEN','FF','WLFI','SKY','2Z','MET','LAUNCHCOIN','CC','HYPE','WLD','BCH','TON','ZRO','ARB',
    'AI16Z','DYDX','XPL','XAU','WIF','SYRUP','ZEC','1000BONK','APEX','ZK','DOLO','MNT','ZORA',
    'POL','IP','DOGE','1000SHIB','1000TOSHI','PENDLE','VVV','FIL','CRO','LINEA','ONDO','HBAR',
    'POPCAT','STBL','OP','1000PEPE','TRX','ENA','MORPHO','BERA','USDCHF','LTC','XMR','JUP',
    'EIGEN','KAITO','TAO','PUMP','XAG','PROVE','FARTCOIN','USDJPY','BNB','MKR','MEGA','XRP',
    'TIA','S','DOT','EURUSD','ASTER','NEAR','0G','AAVE','LDO','NMR','SPX','RESOLV','AVAX','CRV',
    'SOL','AERO','UNI','MON','BTC','ADA','SUI','GBPUSD','ETH','ETHFI','USELESS','PENGU','PYTH',
    'GRASS','YZY','VIRTUAL'
}

_TOKEN_RE = re.compile(r'(?:^|[^A-Za-z0-9])(?:[$#@])?([A-Za-z0-9]+)\b')

def extract_ticker(text: str) -> dict:

    candidates = _TOKEN_RE.findall(text)
    seen = set()
    found = []
    for c in candidates:
        t = c.upper()
        if t in _TICKERS and t not in seen:
            seen.add(t)
            found.append(t)
    return {"has_ticker": bool(found), "ticker": found}


if __name__ == "__main__":
    string_text = """
早上又一個超噁心的破底翻，翻了4次才翻上來，但我沒太大興趣了，這次真的有被噁心到

Flip long條件：
1）Overall liq做空轉負時做空難度上升
2）reclaim 105k
    """
    print(extract_ticker(string_text))