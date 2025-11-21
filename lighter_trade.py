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

logging.basicConfig(level=logging.INFO)

BASE_URL = "https://mainnet.zklighter.elliot.ai"
API_KEY_PRIVATE_KEY = os.getenv("LIGHTER_API_PRIVATE_KEY")
ACCOUNT_INDEX = int(os.getenv("LIGHTER_ACCOUNT_INDEX", "0"))
API_KEY_INDEX = int(os.getenv("LIGHTER_API_KEY_INDEX", "2"))

# Trading configuration
SYMBOL = os.getenv("LIGHTER_SYMBOL", "SOL").upper()
INPUT_AMOUNT = float(os.getenv("LIGHTER_INPUT_AMOUNT", "10"))  # USD margin
IS_ASK = False  # False=BUY(long), True=SELL(short)
TP_PCT = float(os.getenv("LIGHTER_TP_PCT", "0.01"))
SL_PCT = float(os.getenv("LIGHTER_SL_PCT", "0.01"))
LEVERAGE = float(os.getenv("LIGHTER_LEVERAGE", "5"))
MARGIN_MODE = os.getenv("LIGHTER_MARGIN_MODE", "cross")  # cross | isolated


def trim_exception(e: Exception) -> str:
    return str(e).strip().split("\n")[-1]


async def _http_get_json(url: str, headers: dict | None = None):
    def _do():
        req = Request(url, headers=headers or {"User-Agent": "PawXAI-Trade/1.0"})
        with urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8"))

    return await asyncio.to_thread(_do)


def _symbol_to_external_ids(symbol: str):
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
    cg_id, binance_sym, okx_inst_id = _symbol_to_external_ids(symbol)

    if cg_id:
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
            data = await _http_get_json(url)
            price = float(data[cg_id]["usd"])
            if price > 0:
                return price
        except Exception:
            pass

    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={binance_sym}"
        data = await _http_get_json(url)
        price = float(data.get("price"))
        if price > 0:
            return price
    except Exception:
        pass

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


def _get_attr(obj, names, default=None):
    for n in names:
        v = getattr(obj, n, None) if obj is not None else None
        if v is not None:
            return v
    return default


def _resolve_margin_mode_param(mode_value) -> int | object:
    try:
        if isinstance(mode_value, bool):
            return 1 if mode_value else 0
        if isinstance(mode_value, int):
            return int(mode_value)
        val = str(mode_value).strip().lower()
    except Exception:
        return 0

    mm_enum = getattr(lighter, "MarginMode", None)
    if mm_enum is not None:
        cross_enum = getattr(mm_enum, "CROSS", None)
        iso_enum = getattr(mm_enum, "ISOLATED", None)
        if val in ("cross", "x", "c", "0", "false"):
            return cross_enum if cross_enum is not None else 0
        if val in ("isolated", "iso", "i", "1", "true"):
            return iso_enum if iso_enum is not None else 1

    cross_const = getattr(lighter, "MARGIN_MODE_CROSS", None)
    iso_const = getattr(lighter, "MARGIN_MODE_ISOLATED", None)
    if val in ("cross", "x", "c", "0", "false"):
        return cross_const if cross_const is not None else 0
    if val in ("isolated", "iso", "i", "1", "true"):
        return iso_const if iso_const is not None else 1

    return cross_const if cross_const is not None else 0


LAST_CLIENT_ORDER_INDEX = 0


def _next_client_order_index() -> int:
    global LAST_CLIENT_ORDER_INDEX
    now = int(time.time() * 1000)
    if now <= LAST_CLIENT_ORDER_INDEX:
        now = LAST_CLIENT_ORDER_INDEX + 1
    LAST_CLIENT_ORDER_INDEX = now
    return now


async def _submit_with_retry(method, **kwargs):
    attempts = 3
    last = (None, None, "retry not attempted")
    for i in range(attempts):
        kwargs["client_order_index"] = _next_client_order_index()
        try:
            tx = await method(**kwargs)
        except Exception as e:
            last = (None, None, str(e))
            await asyncio.sleep(0.75 * (i + 1))
            continue

        tx_resp, tx_hash, err = tx if isinstance(tx, tuple) and len(tx) >= 3 else (None, None, tx)
        last = (tx_resp, tx_hash, err)
        if err is None:
            return last
        if "invalid nonce" in str(err).lower():
            await asyncio.sleep(0.75 * (i + 1))
            continue
        return last
    return last


async def fetch_market(symbol: str, api_client):
    order_api = lighter.OrderApi(api_client)
    books = await order_api.order_books()
    target = symbol.upper().replace("_", "-")

    market_index = None
    market_info = None
    for ob in getattr(books, "order_books", []):
        mi = getattr(ob, "market_info", None)
        sym = getattr(ob, "symbol", None) or getattr(mi, "symbol", None)
        if sym == "ETH":
            print("ETH object :", ob)
        if sym == "BTC":
            print("BTC object :", ob)
        if sym and str(sym).upper().replace("_", "-") == target:
            idx = (
                getattr(mi, "index", None)
                or getattr(ob, "market_index", None)
                or getattr(ob, "market_id", None)
            )
            if idx is None:
                raise RuntimeError(f"Found {symbol} but index missing in payload: {ob}")
            market_index = int(idx)
            market_info = mi or ob
            break

    if market_index is None:
        raise RuntimeError(f"Market '{symbol}' not found")

    size_decimals = _get_attr(market_info, ["supported_size_decimals", "size_decimals", "base_decimals"], None)
    price_decimals = _get_attr(market_info, ["supported_price_decimals", "price_decimals", "quote_decimals"], None)
    quote_decimals = _get_attr(market_info, ["supported_quote_decimals", "quote_decimals"], None)
    lot_size_int = int(_get_attr(market_info, ["lot_size_int", "lot_size", "base_step", "base_lot_size"], 1))
    min_base_amount = _get_attr(market_info, ["min_base_amount", "min_base", "min_size"], None)
    min_quote_amount = _get_attr(market_info, ["min_quote_amount", "min_quote", "min_notional"], None)

    base_scale = int(10 ** int(size_decimals)) if size_decimals is not None else int(
        _get_attr(market_info, ["base_scale", "base_scale_int", "base_precision", "base_decimals", "base_asset_scale"], 1)
    )
    price_scale = int(10 ** int(price_decimals)) if price_decimals is not None else int(
        _get_attr(market_info, ["price_scale", "price_scale_int", "price_precision", "price_decimals", "quote_asset_scale"], 1)
    )
    quote_scale = int(10 ** int(quote_decimals)) if quote_decimals is not None else price_scale

    return {
        "market_index": market_index,
        "base_scale": base_scale,
        "price_scale": price_scale,
        "quote_scale": quote_scale,
        "lot_size_int": lot_size_int,
        "min_base_amount": min_base_amount,
        "min_quote_amount": min_quote_amount,
    }


def compute_size_and_prices(ext_usd: float, usd_notional: float, meta: dict, is_ask: bool):
    base_scale = meta["base_scale"]
    price_scale = meta["price_scale"]
    quote_scale = meta["quote_scale"]
    lot_size_int = meta["lot_size_int"] or 1
    min_base_amount = meta["min_base_amount"]
    min_quote_amount = meta["min_quote_amount"]

    entry_estimate_int = int(round(ext_usd * float(price_scale)))

    min_base_amount_int = int(math.ceil(float(min_base_amount) * base_scale)) if min_base_amount is not None else 1
    min_quote_amount_int = int(math.ceil(float(min_quote_amount) * quote_scale)) if min_quote_amount is not None else 0

    raw_base_amount = (usd_notional * float(base_scale) * float(price_scale)) / max(float(entry_estimate_int), 1.0)
    base_amount_int = max(int(raw_base_amount), int(min_base_amount_int))

    if lot_size_int and int(lot_size_int) > 1:
        base_amount_int = max(int(min_base_amount_int), (base_amount_int // int(lot_size_int)) * int(lot_size_int))

    actual_quote_usd = (base_amount_int / float(base_scale)) * (entry_estimate_int / float(price_scale))
    actual_quote_int = int(math.floor(actual_quote_usd * quote_scale))
    if min_quote_amount_int > 0 and actual_quote_int < min_quote_amount_int:
        needed_base = int(math.ceil((min_quote_amount_int / float(quote_scale)) * float(base_scale) * float(price_scale) / float(entry_estimate_int)))
        base_amount_int = max(base_amount_int, needed_base)
        if lot_size_int and int(lot_size_int) > 1:
            base_amount_int = ((base_amount_int + int(lot_size_int) - 1) // int(lot_size_int)) * int(lot_size_int)

    # Side-aware slippage buffer: buys tolerate slightly higher, sells slightly lower
    worst_avg_price_int = int(entry_estimate_int * (0.995 if is_ask else 1.005))
    return base_amount_int, entry_estimate_int, worst_avg_price_int


async def main():
    if not API_KEY_PRIVATE_KEY or ACCOUNT_INDEX is None:
        raise RuntimeError("Missing LIGHTER_API_PRIVATE_KEY or LIGHTER_ACCOUNT_INDEX env")

    api_client = lighter.ApiClient(configuration=lighter.Configuration(host=BASE_URL))
    client = lighter.SignerClient(
        url=BASE_URL,
        private_key=API_KEY_PRIVATE_KEY,
        account_index=ACCOUNT_INDEX,
        api_key_index=API_KEY_INDEX,
    )

    err = client.check_client()
    if err is not None:
        print(f"CheckClient error: {trim_exception(err)}")
        return

    meta = await fetch_market(SYMBOL, api_client)
    market_index = meta["market_index"]
    logging.info(f"Resolved {SYMBOL} market_index={market_index}")

    try:
        ext_usd = await _get_external_price_usd(SYMBOL)
    except Exception as e:
        logging.error(f"Fetch external price failed: {trim_exception(e)}")
        return

    usd_notional = float(INPUT_AMOUNT) * float(LEVERAGE)
    size_int, entry_price_int, worst_price_int = compute_size_and_prices(ext_usd, usd_notional, meta, IS_ASK)
    tp_trigger_int = int(entry_price_int * (1 + TP_PCT))
    sl_trigger_int = int(entry_price_int * (1 - SL_PCT))

    logging.info(
        f"Sizing: symbol={SYMBOL}, margin=${INPUT_AMOUNT}, leverage={LEVERAGE}x, notional=${usd_notional}, size_int={size_int}, entry_int={entry_price_int}"
    )

    try:
        if hasattr(client, "update_leverage"):
            resolved_mode = _resolve_margin_mode_param(MARGIN_MODE)
            tx, lev_tx_hash, err = await client.update_leverage(
                market_index=market_index,
                leverage=int(LEVERAGE),
                margin_mode=resolved_mode,
            )
            print(f"Leverage set to {LEVERAGE}x; tx_hash={lev_tx_hash} err={err}")
    except Exception as e:
        logging.warning(f"update_leverage failed (non-fatal): {trim_exception(e)}")

    # Market order (use names observed in local code: base_amount, avg_execution_price)
    tx_resp, tx_hash, err = await _submit_with_retry(
        client.create_market_order,
        market_index=market_index,
        base_amount=size_int,
        avg_execution_price=worst_price_int,
        is_ask=IS_ASK,
    )
    if err is not None:
        logging.error(f"Market order failed: {trim_exception(err)}")
        return
    print(f"Market order ok; tx_hash={tx_hash}")

    # Take Profit on opposite side
    tp_resp, tp_hash, tp_err = await _submit_with_retry(
        client.create_tp_limit_order,
        market_index=market_index,
        base_amount=size_int,
        trigger_price=tp_trigger_int,
        price=tp_trigger_int,
        is_ask=not IS_ASK,
    )
    if tp_err is not None:
        # Fallback: generic order type (if SDK supports)
        try:
            ORDER_TYPE_TAKE_PROFIT = getattr(lighter.SignerClient, "ORDER_TYPE_TAKE_PROFIT", None)
            if ORDER_TYPE_TAKE_PROFIT is not None and hasattr(client, "create_order"):
                tp2_resp, tp2_hash, tp2_err = await _submit_with_retry(
                    client.create_order,
                    market_index=market_index,
                    amount=size_int,
                    is_ask=not IS_ASK,
                    order_type=ORDER_TYPE_TAKE_PROFIT,
                    trigger_price=tp_trigger_int,
                    price=tp_trigger_int,
                )
                print("Create TP Order (fallback):", tp2_resp, tp2_hash, tp2_err)
            else:
                logging.warning("TP fallback not available in SDK")
        except Exception as e:
            logging.warning(f"TP fallback failed: {trim_exception(e)}")
    else:
        print("Create TP Limit Order:", tp_resp, tp_hash, tp_err)

    # Stop Loss on opposite side
    sl_resp, sl_hash, sl_err = await _submit_with_retry(
        client.create_sl_limit_order,
        market_index=market_index,
        base_amount=size_int,
        trigger_price=sl_trigger_int,
        price=sl_trigger_int,
        is_ask=not IS_ASK,
    )
    if sl_err is not None:
        try:
            ORDER_TYPE_STOP_LOSS = getattr(lighter.SignerClient, "ORDER_TYPE_STOP_LOSS", None)
            if ORDER_TYPE_STOP_LOSS is not None and hasattr(client, "create_order"):
                sl2_resp, sl2_hash, sl2_err = await _submit_with_retry(
                    client.create_order,
                    market_index=market_index,
                    amount=size_int,
                    is_ask=not IS_ASK,
                    order_type=ORDER_TYPE_STOP_LOSS,
                    trigger_price=sl_trigger_int,
                    price=sl_trigger_int,
                )
                print("Create SL Order (fallback):", sl2_resp, sl2_hash, sl2_err)
            else:
                logging.warning("SL fallback not available in SDK")
        except Exception as e:
            logging.warning(f"SL fallback failed: {trim_exception(e)}")
    else:
        print("Create SL Limit Order:", sl_resp, sl_hash, sl_err)

    # Cleanly close sessions to avoid 'Unclosed client session'
    try:
        await api_client.close()
    except Exception:
        pass
    try:
        if hasattr(client, "close") and callable(getattr(client, "close")):
            await client.close()
    except Exception:
        pass
    try:
        api_attr = getattr(client, "api", None)
        if api_attr is not None and hasattr(api_attr, "close"):
            await api_attr.close()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())