import asyncio
import os
import logging
import lighter
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.DEBUG)

# The API_KEY_PRIVATE_KEY provided belongs to a dummy account registered on Testnet.
# It was generated using the setup_system.py script, and serves as an example.
BASE_URL = "https://testnet.zklighter.elliot.ai"
API_KEY_PRIVATE_KEY = os.getenv("LIGHTER_API_PRIVATE_KEY")
ACCOUNT_INDEX = 453
API_KEY_INDEX = 3



def trim_exception(e: Exception) -> str:
    return str(e).strip().split("\n")[-1]


async def main():
    client = lighter.SignerClient(
        url=BASE_URL,
        private_key=API_KEY_PRIVATE_KEY,
        account_index=ACCOUNT_INDEX,
        api_key_index=API_KEY_INDEX,
    )

    tx = await client.create_market_order(
        market_index=0,
        client_order_index=0,
        base_amount=1000,  # 0.1 ETH
        avg_execution_price=170000,  # $1700 -- worst acceptable price for the order
        is_ask=True,
    )
    print("Create Order Tx:", tx)
    

    tx = await client.create_tp_order(
        market_index=0,
        client_order_index=0,
        base_amount=1000,  # 0.1 ETH
        trigger_price=500000,
        price=500000,
        is_ask=True
    )
    print("Create Order Tx:", tx)

    tx = await client.create_sl_order(
        market_index=0,
        client_order_index=0,
        base_amount=1000,  # 0.1 ETH
        trigger_price=500000,
        price=500000,
        is_ask=False
    )
    print("Create Order Tx:", tx)

    tx = await client.create_tp_limit_order(
        market_index=0,
        client_order_index=0,
        base_amount=1000,  # 0.1 ETH
        trigger_price=500000,
        price=500000,
        is_ask=True
    )

    tx = await client.create_sl_limit_order(
        market_index=0,
        client_order_index=0,
        base_amount=1000,  # 0.1 ETH
        trigger_price=500000,
        price=500000,
        is_ask=False
    )
    print("Create Order Tx:", tx)
    await client.close()



if __name__ == "__main__":
    asyncio.run(main())