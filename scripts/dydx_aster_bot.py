import asyncio
import os
import random
import logging
import time
from decimal import Decimal, getcontext

from dydx_v4_client.network import make_testnet
from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
from dydx_v4_client.node.client import NodeClient
from dydx_v4_client.wallet import Wallet
from dydx_v4_client.node.market import Market, OrderType, OrderExecution
from dydx_v4_client import MAX_CLIENT_ID, OrderFlags
from v4_proto.dydxprotocol.clob.order_pb2 import Order as OrderProto

from dotenv import load_dotenv

load_dotenv()

# Set Decimal precision
getcontext().prec = 18

# ==========================
# Configuration Section
# ==========================

# Account credentials
DYDX_MNEMONIC = os.getenv("DYDX_MNEMONIC")
ADDRESS = os.getenv("DYDX_ADDRESS")

print(DYDX_MNEMONIC)

# Network endpoints (testnet). Node URL must NOT include http(s)://
DYDX_NODE_URL = os.getenv("DYDX_NODE_URL")  # e.g., "oegs.dydx.trade:443" or a testnet gRPC host:port
DYDX_INDEXER_HTTP = os.getenv("DYDX_INDEXER_HTTP")  # e.g., "https://indexer.dydx.trade"
DYDX_INDEXER_WS = os.getenv("DYDX_INDEXER_WS")  # e.g., "wss://indexer.dydx.trade/v4/ws"

def _strip_scheme(host: str | None) -> str | None:
    if not host:
        return host
    for p in ("https://", "http://", "grpc://", "grpcs://"):
        if host.startswith(p):
            host = host[len(p):]
            break
    return host.strip("/")

# Build network using latest client helpers (only include non-None endpoints)
_net_kwargs = {}
if DYDX_NODE_URL:
    _net_kwargs["node_url"] = _strip_scheme(DYDX_NODE_URL)
if DYDX_INDEXER_HTTP:
    _net_kwargs["rest_indexer"] = DYDX_INDEXER_HTTP
if DYDX_INDEXER_WS:
    _net_kwargs["websocket_indexer"] = DYDX_INDEXER_WS
NETWORK = make_testnet(**_net_kwargs)
SUBACCOUNT_NUMBER = 0  # 0 = cross margin; for isolated use 128+ if supported

# Market & trading parameters
MARKET_KEYWORD = "ETH"     
NOTIONAL_USD = Decimal("100")  # how much USD to use in this trade
LEVERAGE = Decimal("5")        # target leverage
TAKE_PROFIT_PCT = Decimal("0.01")  # +1%
STOP_LOSS_PCT = Decimal("0.01")    # -1%

# Activity & logging
LOG_FILE = "trade_activity.log"
DEBUG_MODE = True  # if True, print debug info
SIMULATION_MODE = False  # if True, simulate without placing orders

# ==========================
# Logging Setup
# ==========================

logging.basicConfig(
    filename=LOG_FILE,
    filemode="a",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def log(msg):
    """Log to both console and file."""
    print(msg)
    logging.info(msg)

# ==========================
# Helper Functions
# ==========================

async def find_market(indexer: IndexerClient, keyword: str):
    """Find a perpetual market containing the given keyword."""
    resp = await indexer.markets.get_perpetual_markets()
    markets = resp.get("markets", {})
    for market_id, m in markets.items():
        if keyword.upper() in market_id.upper() or keyword.upper() in (m.get("name", "").upper()):
            return market_id, m
    return None, None

def get_price(market_obj: dict):
    """Try to extract the current reference price from market data."""
    for k in ("oraclePrice", "indexPrice", "markPrice", "price", "lastPrice"):
        if market_obj.get(k) is not None:
            return Decimal(str(market_obj[k]))
    return None

# ==========================
# Main Trading Logic
# ==========================

async def trade_aster():
    """Execute a long position with TP/SL on dYdX."""
    if not DYDX_MNEMONIC:
        raise RuntimeError("DYDX_MNEMONIC is not set. Add it to your environment or .env.")
    if not ADDRESS:
        raise RuntimeError("DYDX_ADDRESS is not set. Add it to your environment or .env.")

    # 1. Connect to node and indexer (per latest SDK)
    node = await NodeClient.connect(NETWORK.node)
    indexer = IndexerClient(NETWORK.rest_indexer)

    # 2. Find the ASTER market
    market_id, market_obj = await find_market(indexer, MARKET_KEYWORD)
    if not market_id:
        raise RuntimeError(f"Market with keyword '{MARKET_KEYWORD}' not found.")
    log(f"Selected market: {market_id}")

    # 3. Get reference price
    price = get_price(market_obj)
    if price is None:
        raise RuntimeError("Cannot get reference price from market data.")
    log(f"Current price: {price}")

    # 4. Calculate trade size
    size = (NOTIONAL_USD / price).quantize(Decimal("0.00000001"))
    margin_required = (NOTIONAL_USD / LEVERAGE).quantize(Decimal("0.00000001"))
    log(f"Trade notional: ${NOTIONAL_USD}, size: {size}, required margin: ${margin_required}")

    # 5. Create wallet
    wallet = await Wallet.from_mnemonic(node, DYDX_MNEMONIC, ADDRESS)
    acc_info = await node.get_account(ADDRESS)
    wallet.sequence = acc_info.sequence

    # 6. Prepare market object
    market = Market(market_obj)
    current_block = await node.latest_block_height()

    # 7. Create open market order (LONG)
    client_id = random.randint(0, MAX_CLIENT_ID)
    order_id = market.order_id(ADDRESS, SUBACCOUNT_NUMBER, client_id, OrderFlags.SHORT_TERM)

    # Use Market.order helper to construct the proto Order
    open_order = market.order(
        order_id=order_id,
        order_type=OrderType.MARKET,
        side=OrderProto.Side.SIDE_BUY,  # Long
        size=float(size),
        price=0.0,
        time_in_force=OrderProto.TimeInForce.TIME_IN_FORCE_IOC,
        reduce_only=False,
        post_only=False,
        good_til_block=current_block + 20,
        execution=OrderExecution.IOC,
    )

    if SIMULATION_MODE:
        log("[SIMULATION] Would place open order (LONG)")
    else:
        tx_open = await node.place_order(wallet=wallet, order=open_order)
        wallet.sequence += 1
        log(f"Open order placed: tx={tx_open}")

    # 8. Compute TP/SL prices
    entry_price = price
    tp_price = (entry_price * (1 + TAKE_PROFIT_PCT)).quantize(Decimal("0.00000001"))
    sl_price = (entry_price * (1 - STOP_LOSS_PCT)).quantize(Decimal("0.00000001"))
    log(f"TP: {tp_price}, SL: {sl_price}")

    # 9. Take Profit Order (conditional market)
    client_id_tp = random.randint(0, MAX_CLIENT_ID)
    order_id_tp = market.order_id(ADDRESS, SUBACCOUNT_NUMBER, client_id_tp, OrderFlags.CONDITIONAL)
    # Stateful orders must use good_til_block_time (epoch seconds), not good_til_block
    gtbt_tp = int(time.time()) + 24 * 60 * 60  # 24h validity
    tp_order = market.order(
        order_id=order_id_tp,
        order_type=OrderType.TAKE_PROFIT_MARKET,
        side=OrderProto.Side.SIDE_SELL,
        size=float(size),
        price=0.0,
        time_in_force=OrderProto.TimeInForce.TIME_IN_FORCE_IOC,
        reduce_only=True,
        post_only=False,
        good_til_block_time=gtbt_tp,
        conditional_order_trigger_subticks=market.calculate_subticks(float(tp_price)),
        execution=OrderExecution.IOC,
    )

    if SIMULATION_MODE:
        log("[SIMULATION] Would place TP order")
    else:
        tx_tp = await node.place_order(wallet=wallet, order=tp_order)
        wallet.sequence += 1
        code = getattr(tx_tp, "code", 0)
        if code and int(code) != 0:
            log(f"Take Profit order failed: tx={tx_tp}")
        else:
            log(f"Take Profit order placed: tx={tx_tp}")

    # 10. Stop Loss Order (conditional market)
    client_id_sl = random.randint(0, MAX_CLIENT_ID)
    order_id_sl = market.order_id(ADDRESS, SUBACCOUNT_NUMBER, client_id_sl, OrderFlags.CONDITIONAL)
    gtbt_sl = int(time.time()) + 24 * 60 * 60  # 24h validity
    sl_order = market.order(
        order_id=order_id_sl,
        order_type=OrderType.STOP_MARKET,
        side=OrderProto.Side.SIDE_SELL,
        size=float(size),
        price=0.0,
        time_in_force=OrderProto.TimeInForce.TIME_IN_FORCE_IOC,
        reduce_only=True,
        post_only=False,
        good_til_block_time=gtbt_sl,
        conditional_order_trigger_subticks=market.calculate_subticks(float(sl_price)),
        execution=OrderExecution.IOC,
    )

    if SIMULATION_MODE:
        log("[SIMULATION] Would place SL order")
    else:
        tx_sl = await node.place_order(wallet=wallet, order=sl_order)
        wallet.sequence += 1
        code = getattr(tx_sl, "code", 0)
        if code and int(code) != 0:
            log(f"Stop Loss order failed: tx={tx_sl}")
        else:
            log(f"Stop Loss order placed: tx={tx_sl}")

    log("✅ Trade execution completed successfully.")
    return {
        "market": market_id,
        "size": str(size),
        "entry_price": str(entry_price),
        "tp_price": str(tp_price),
        "sl_price": str(sl_price)
    }

# ==========================
# Main Entry Point
# ==========================

if __name__ == "__main__":
    try:
        result = asyncio.run(trade_aster())
        log(f"Trade summary: {result}")
    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        print(f"❌ Error occurred: {e}")
