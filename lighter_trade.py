import asyncio
import logging
import os
import time
import math
import json
from urllib.request import urlopen, Request
import lighter
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.DEBUG)

BASE_URL = "https://testnet.zklighter.elliot.ai"
API_KEY_PRIVATE_KEY = os.getenv("LIGHTER_API_PRIVATE_KEY")
ACCOUNT_INDEX = 453
API_KEY_INDEX = 3

# Trading configuration
SYMBOL = "BTC"       # Change your Symbol
INPUT_AMOUNT = 100  # USD margin to allocate
IS_ASK = False    # False=BUY(long), True=SELL(short);
TP_PCT = 0.01        # 1% take-profit
SL_PCT = 0.01        # 1% stop-loss
# Notional-based sizing and leverage
LEVERAGE = 5         # Target leverage 2x
MARGIN_MODE = "cross" # Margin mode for leverage updates, if supported


async def compute_order_params(symbol: str, usd_amount: float, api_client) -> tuple[int, int, int]:
    """
    Returns (market_index, base_amount_int, avg_execution_price_int) for a USD notional market order.
    - Uses order_books to resolve scales, min amounts, lot size.
    - Uses external price APIs to estimate USD price, then applies a small slippage buffer.
    """
    order_api = lighter.OrderApi(api_client)
    books = await order_api.order_books()

    target = symbol.upper().replace("_", "-")
    market_index = None
    market_info = None

    # Resolve market_index and market_info for the target symbol
    for ob in getattr(books, "order_books", []):
        mi = getattr(ob, "market_info", None)
        sym = (
            getattr(mi, "symbol", None)
            or getattr(mi, "name", None)
            or getattr(ob, "symbol", None)
            or getattr(ob, "name", None)
        )
        if sym and str(sym).upper().replace("_", "-") == target:
            idx = (
                getattr(mi, "index", None)
                or getattr(ob, "market_index", None)
                or getattr(ob, "market_id", None)
            )
            market_index = int(idx)
            market_info = mi or ob
            break

    if market_index is None:
        raise RuntimeError(f"Market '{symbol}' not found.")

    # Extract decimals/scales and constraints
    size_decimals = (
        getattr(market_info, "supported_size_decimals", None)
        or getattr(market_info, "size_decimals", None)
        or getattr(market_info, "base_decimals", None)
    )
    price_decimals = (
        getattr(market_info, "supported_price_decimals", None)
        or getattr(market_info, "price_decimals", None)
        or getattr(market_info, "quote_decimals", None)
    )
    quote_decimals = (
        getattr(market_info, "supported_quote_decimals", None)
        or getattr(market_info, "quote_decimals", None)
    )

    lot_size_int = (
        getattr(market_info, "lot_size_int", None)
        or getattr(market_info, "lot_size", None)
        or getattr(market_info, "base_step", None)
        or getattr(market_info, "base_lot_size", None)
        or 1
    )
    min_base_amount = (
        getattr(market_info, "min_base_amount", None)
        or getattr(market_info, "min_base", None)
        or getattr(market_info, "min_size", None)
    )
    min_quote_amount = (
        getattr(market_info, "min_quote_amount", None)
        or getattr(market_info, "min_quote", None)
        or getattr(market_info, "min_notional", None)
    )

    base_scale = int(10 ** int(size_decimals)) if size_decimals is not None else (
        getattr(market_info, "base_scale", None)
        or getattr(market_info, "base_scale_int", None)
        or getattr(market_info, "base_precision", None)
        or getattr(market_info, "base_asset_scale", None)
        or 1
    )
    price_scale = int(10 ** int(price_decimals)) if price_decimals is not None else (
        getattr(market_info, "price_scale", None)
        or getattr(market_info, "price_scale_int", None)
        or getattr(market_info, "price_precision", None)
        or getattr(market_info, "quote_asset_scale", None)
        or 1
    )
    quote_scale = int(10 ** int(quote_decimals)) if quote_decimals is not None else price_scale

    # External USD spot price → scaled entry price
    ext_usd = await _get_external_price_usd(symbol)
    entry_estimate = int(round(ext_usd * float(price_scale)))

    # Compute base amount from USD notional, enforce min_base and lot size
    min_base_amount_int = int(math.ceil(float(min_base_amount) * base_scale)) if min_base_amount is not None else 1
    min_quote_amount_int = int(math.ceil(float(min_quote_amount) * quote_scale)) if min_quote_amount is not None else 0

    raw_base_amount = (usd_amount * float(base_scale) * float(price_scale)) / max(float(entry_estimate), 1.0)
    base_amount_int = max(int(raw_base_amount), int(min_base_amount_int))
    if lot_size_int and int(lot_size_int) > 1:
        base_amount_int = max(int(min_base_amount_int), (base_amount_int // int(lot_size_int)) * int(lot_size_int))

    # Ensure min_quote notional requirement (if present)
    actual_quote = (base_amount_int / float(base_scale)) * (entry_estimate / float(price_scale))  # in USD
    actual_quote_int = int(math.floor(actual_quote * quote_scale))
    if min_quote_amount_int > 0 and actual_quote_int < min_quote_amount_int:
        # Increase base amount to meet min_quote; round up to lot size
        needed_base = int(math.ceil((min_quote_amount_int / float(quote_scale)) * float(base_scale) * float(price_scale) / float(entry_estimate)))
        base_amount_int = max(base_amount_int, needed_base)
        if lot_size_int and int(lot_size_int) > 1:
            base_amount_int = ((base_amount_int + int(lot_size_int) - 1) // int(lot_size_int)) * int(lot_size_int)

    # Worst acceptable average execution price (add small slippage buffer)
    slippage_pct = 0.005  # 0.5%
    avg_execution_price_int = int(entry_estimate * (1 + slippage_pct))

    return market_index, base_amount_int, avg_execution_price_int

def trim_exception(e: Exception) -> str:
    return str(e).strip().split("\n")[-1]


def _get_first_attr(obj, names, default=None):
    for n in names:
        v = getattr(obj, n, None) if obj is not None else None
        if v is not None:
            return v
    return default


async def _http_get_json(url: str, headers: dict | None = None):
    """Perform a simple HTTP GET and decode JSON (run in thread to avoid blocking loop)."""
    def _do():
        req = Request(url, headers=headers or {"User-Agent": "PawXAI-Trade/1.0"})
        with urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8"))

    return await asyncio.to_thread(_do)


def _symbol_to_external_ids(symbol: str):
    """Map symbol to external provider identifiers (CoinGecko id, Binance symbol, OKX instId)."""
    sym = symbol.upper()
    coingecko_ids = {
        "SUI": "sui",
        "SOL": "solana",
        "LTC": "litecoin",
        "BTC": "bitcoin",
        "ETH": "ethereum",
    }
    binance_symbol = f"{sym}USDT"
    okx_inst_id = f"{sym}-USDT"
    return coingecko_ids.get(sym), binance_symbol, okx_inst_id


async def _get_external_price_usd(symbol: str) -> float:
    """Get USD spot price via public APIs (CoinGecko → Binance → OKX)."""
    cg_id, binance_sym, okx_inst_id = _symbol_to_external_ids(symbol)

    # Try CoinGecko simple price
    if cg_id:
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
            data = await _http_get_json(url)
            price = float(data[cg_id]["usd"])  # docs: https://docs.coingecko.com/reference/simple-price
            if price > 0:
                return price
        except Exception:
            pass

    # Fallback: Binance spot ticker price (USDT proxy for USD)
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={binance_sym}"
        data = await _http_get_json(url)
        price = float(data.get("price"))  # docs: https://dev.binance.vision/t/understanding-the-binance-api-for-symbols-and-prices/6253
        if price > 0:
            return price
    except Exception:
        pass

    # Fallback: OKX ticker last price
    try:
        url = f"https://www.okx.com/api/v5/market/ticker?instId={okx_inst_id}"
        data = await _http_get_json(url)
        arr = data.get("data")
        if isinstance(arr, list) and arr:
            price = float(arr[0].get("last"))
            if price > 0:
                return price
    except Exception:
        pass

    raise RuntimeError(f"Failed to fetch external USD price for {symbol}")


async def _fetch_order_book_details(order_api, market_index: int):
    """Robustly fetch order book detail for a given market_index with multiple fallbacks.

    Some SDK versions expose different method names or parameter signatures. We try several
    common variants and return the first successful response.
    """
    # candidate method names with kwargs to try in order
    attempts = [
        ("order_book_details_index", {"market_index": market_index}),
        ("order_book_details_index", {"market_index": str(market_index)}),
        ("order_book_details", {"market_index": market_index}),
        ("order_book_details", {"market_index": str(market_index)}),
        ("order_book_details_id", {"market_id": market_index}),
        ("order_book_details_id", {"market_id": str(market_index)}),
        ("order_book_details", {"id": market_index}),
        ("order_book_details", {"id": str(market_index)}),
    ]

    last_err = None
    for name, kwargs in attempts:
        try:
            if hasattr(order_api, name):
                func = getattr(order_api, name)
                return await func(**kwargs)
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(
        f"Failed to fetch order_book details for market_index={market_index}; last_err={trim_exception(last_err) if last_err else 'unknown'}"
    )


# --- Nonce / Client Order Index helpers & retry wrapper ---
LAST_CLIENT_ORDER_INDEX = 0

def _next_client_order_index() -> int:
    """Return a strictly-increasing client_order_index based on epoch ms."""
    global LAST_CLIENT_ORDER_INDEX
    now = int(time.time() * 1000)
    if now <= LAST_CLIENT_ORDER_INDEX:
        now = LAST_CLIENT_ORDER_INDEX + 1
    LAST_CLIENT_ORDER_INDEX = now
    return now

# --- Leverage / Margin helpers ---
def _resolve_margin_mode_param(mode_value) -> int | object:
    """
    Normalize margin_mode to the type expected by SignerClient.update_leverage.

    Tries SDK enums/constants when present, otherwise maps strings to ints:
    - cross -> 0
    - isolated -> 1
    Returns an enum value if available in the installed SDK; otherwise returns int.
    """
    # If already bool/int, convert directly to 0/1
    try:
        if isinstance(mode_value, bool):
            return 1 if mode_value else 0
        if isinstance(mode_value, int):
            return int(mode_value)
        val = str(mode_value).strip().lower()
    except Exception:
        return 0

    # Prefer enum class MarginMode when available
    mm_enum = getattr(lighter, "MarginMode", None)
    if mm_enum is not None:
        cross_enum = getattr(mm_enum, "CROSS", None)
        iso_enum = getattr(mm_enum, "ISOLATED", None)
        if val in ("cross", "x", "c", "0", "false"):
            return cross_enum if cross_enum is not None else 0
        if val in ("isolated", "iso", "i", "1", "true"):
            return iso_enum if iso_enum is not None else 1

    # Fallback to module-level constants when available
    cross_const = getattr(lighter, "MARGIN_MODE_CROSS", None)
    iso_const = getattr(lighter, "MARGIN_MODE_ISOLATED", None)
    if val in ("cross", "x", "c", "0", "false"):
        return cross_const if cross_const is not None else 0
    if val in ("isolated", "iso", "i", "1", "true"):
        return iso_const if iso_const is not None else 1

    # Default to cross
    return cross_const if cross_const is not None else 0


async def _submit_with_retry(method, **kwargs):
    """Submit an order with automatic retry on 'invalid nonce'. Returns (tx, tx_hash, err)."""
    attempts = 3
    last = (None, None, "retry not attempted")
    for i in range(attempts):
        # ensure unique client_order_index each attempt
        kwargs["client_order_index"] = _next_client_order_index()
        try:
            tx = await method(**kwargs)
        except Exception as e:
            last = (None, None, str(e))
            await asyncio.sleep(0.75 * (i + 1))
            continue

        # Expecting tuple (tx, tx_hash, err)
        tx_resp, tx_hash, err = tx if isinstance(tx, tuple) and len(tx) >= 3 else (None, None, tx)
        last = (tx_resp, tx_hash, err)
        if err is None:
            return last
        err_str = str(err).lower()
        if "invalid nonce" in err_str:
            # backoff and retry with a fresh client_order_index
            await asyncio.sleep(0.75 * (i + 1))
            continue
        # other error: return directly
        return last

    return last


async def main():
    api_client = None
    client = None
    try:
        api_client = lighter.ApiClient(configuration=lighter.Configuration(host=BASE_URL))

        client = lighter.SignerClient(
            url=BASE_URL,
            private_key=API_KEY_PRIVATE_KEY,
            account_index=ACCOUNT_INDEX,
            api_key_index=API_KEY_INDEX,
        )

        print("client:", client)

        err = client.check_client()
        if err is not None:
            print(f"CheckClient error: {trim_exception(err)}")
            return

        # Resolve market_index via OrderApi.order_books
        order_api = lighter.OrderApi(api_client)
        books = await order_api.order_books()

        symbols = []
        for ob in getattr(books, "order_books", []):
            sym = getattr(ob, "symbol", None)
            if sym is None:
                mi = getattr(ob, "market_info", None)
                sym = getattr(mi, "symbol", None)
            if sym is not None:
                symbols.append(str(sym))
            if sym == "ETH":
                print("ETH object :", ob)
            if sym == "BTC":
                print("BTC object :", ob)
        print("symbols:", symbols)
        print(type(books))

        target = SYMBOL.upper().replace("_", "-")
        market_index = None

        # The SDK returns a list in books.order_books; each item usually has market_info with index and symbol
        for ob in getattr(books, "order_books", []):
            mi = getattr(ob, "market_info", None)
            sym = (
                getattr(mi, "symbol", None)
                or getattr(mi, "name", None)
                or getattr(ob, "symbol", None)
                or getattr(ob, "name", None)
            )
            if sym and str(sym).upper().replace("_", "-") == target:
                # Fallback to market_id when index is not present in payload
                idx = (
                    getattr(mi, "index", None)
                    or getattr(ob, "market_index", None)
                    or getattr(ob, "market_id", None)
                )
                if idx is None:
                    raise RuntimeError(f"Found {SYMBOL} but index missing in payload: {ob}")
                market_index = int(idx)
                break

        if market_index is None:
            raise RuntimeError(f"Market '{SYMBOL}' not found. Use order_books() to inspect available markets.")

        logging.info(f"Resolved {SYMBOL} market_index={market_index}")

        # Resolve market_info and decimal scales from order_books snapshot
        market_info = None
        try:
            for item in getattr(books, "order_books", []):
                mi2 = getattr(item, "market_info", None)
                idx2 = (
                    getattr(mi2, "index", None)
                    or getattr(item, "market_index", None)
                    or getattr(item, "market_id", None)
                )
                if idx2 is not None and int(idx2) == market_index:
                    market_info = mi2 or item
                    break
        except Exception:
            pass

        size_decimals = _get_first_attr(
            market_info,
            ["supported_size_decimals", "size_decimals", "base_decimals"],
            None,
        )
        price_decimals = _get_first_attr(
            market_info,
            ["supported_price_decimals", "price_decimals", "quote_decimals"],
            None,
        )
        quote_decimals = _get_first_attr(
            market_info,
            ["supported_quote_decimals", "quote_decimals"],
            None,
        )
        lot_size_int = _get_first_attr(
            market_info,
            ["lot_size_int", "lot_size", "base_step", "base_lot_size"],
            1,
        )

        min_base_amount = _get_first_attr(
            market_info,
            ["min_base_amount", "min_base", "min_size"],
            None,
        )
        min_quote_amount = _get_first_attr(
            market_info,
            ["min_quote_amount", "min_quote", "min_notional"],
            None,
        )

        base_scale = int(10 ** int(size_decimals)) if size_decimals is not None else _get_first_attr(
            market_info,
            ["base_scale", "base_scale_int", "base_precision", "base_decimals", "base_asset_scale"],
            1,
        )
        price_scale = int(10 ** int(price_decimals)) if price_decimals is not None else _get_first_attr(
            market_info,
            ["price_scale", "price_scale_int", "price_precision", "price_decimals", "quote_asset_scale"],
            1,
        )

        try:
            ext_usd = await _get_external_price_usd(SYMBOL)
        except Exception as e:
            logging.warning(f"Error :", e)
            ext_usd = None

        # Treat INPUT_AMOUNT as desired margin; size notional = margin * leverage
        usd_margin = float(INPUT_AMOUNT)
        usd_notional = usd_margin * float(LEVERAGE)

        # Compute robust order params using order book metadata
        market_idx2, base_amount_int, entry_estimate = await compute_order_params(
            SYMBOL, usd_notional, api_client
        )

        # Recompute TP/SL off the computed entry estimate
        tp_trigger = int(entry_estimate * (1 + TP_PCT))
        sl_trigger = int(entry_estimate * (1 - SL_PCT))
        logging.info(
            f"Sizing: symbol={SYMBOL}, margin=${usd_margin}, notional=${usd_notional}, base_amount_int={base_amount_int}, entry_estimate={entry_estimate}"
        )

        # Try to set leverage (best-effort) — normalize margin_mode to proper enum/int
        try:
            if hasattr(client, "update_leverage"):
                resolved_mode = _resolve_margin_mode_param(MARGIN_MODE)
                try:
                    tx, lev_tx_hash, err = await client.update_leverage(
                        market_index=market_index,
                        leverage=int(LEVERAGE),
                        margin_mode=resolved_mode,
                    )
                    print(f"Requested leverage {LEVERAGE}x; tx_hash={lev_tx_hash} err={err}")
                except TypeError as te:
                    logging.warning(
                        f"update_leverage TypeError: {trim_exception(te)}; retrying with normalized mode and fallback path"
                    )
                    # Some SDK versions require setting margin mode via a separate call
                    try:
                        if hasattr(client, "update_margin"):
                            tx_mm, mm_tx_hash, mm_err = await client.update_margin(
                                market_index=market_index,
                                margin_mode=resolved_mode,
                            )
                            logging.info(f"Updated margin_mode; tx_hash={mm_tx_hash} err={mm_err}")
                        # Retry leverage without margin_mode parameter
                        tx, lev_tx_hash, err = await client.update_leverage(
                            market_index=market_index,
                            leverage=int(LEVERAGE),
                        )
                        print(f"Requested leverage {LEVERAGE}x; tx_hash={lev_tx_hash} err={err}")
                    except Exception as e2:
                        logging.warning(
                            f"Leverage/margin update retry failed (non-fatal): {trim_exception(e2)}"
                        )
            else:
                logging.info("Leverage update method not found; proceeding without explicit leverage change.")
        except Exception as e:
            logging.warning(f"Failed to update leverage (non-fatal): {trim_exception(e)}")
        
        if IS_ASK == True: # Sell Short
            entry_estimate = int(entry_estimate * 0.95)
        else: # Buy Long
            entry_estimate = int(entry_estimate * 1.05)

        print("market index:", market_index)
        print("current price (USD):", ext_usd)
        print("entry_estimate (scaled):", entry_estimate)
        print("base amount:", base_amount_int)
        print("tp price:", tp_trigger)
        print("sl price:", sl_trigger)
        
        # 1) Market order with unique client_order_index and retry on invalid nonce
        tx_resp, tx_hash, err = await _submit_with_retry(
            client.create_market_order,
            market_index=market_index,
            base_amount=base_amount_int,
            avg_execution_price=entry_estimate,
            is_ask=IS_ASK,
        )
        print("Create Market Order:", tx_resp, tx_hash, err)


        tx_resp, tx_hash, err = await _submit_with_retry(
            client.create_tp_limit_order,
            market_index=market_index,
            base_amount=base_amount_int,
            trigger_price=tp_trigger,
            price=tp_trigger,
            is_ask=not IS_ASK,
        )
        print("Create TP Limit Order:", tx_resp, tx_hash, err)

        tx_resp, tx_hash, err = await _submit_with_retry(
            client.create_sl_limit_order,
            market_index=market_index,
            base_amount=base_amount_int,
            trigger_price=sl_trigger,
            price=sl_trigger,
            is_ask=not IS_ASK,
        )
        print("Create SL Limit Order:", tx_resp, tx_hash, err)

        # Optional: create auth token for API/WS methods that require auth
        auth, err = client.create_auth_token_with_expiry(lighter.SignerClient.DEFAULT_10_MIN_AUTH_EXPIRY)
        print(f"Auth token err={err}")
    finally:
        # Ensure sessions are closed even on error paths
        try:
            if client is not None:
                await client.close()
        except Exception:
            pass
        try:
            if api_client is not None:
                await api_client.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())