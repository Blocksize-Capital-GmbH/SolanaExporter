import os

import requests
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import MemcmpOpts
from solders.pubkey import Pubkey as PublicKey

STAKE_PROGRAM_ID: PublicKey = PublicKey.from_string(
    "Stake11111111111111111111111111111111111111"
)


async def main():
    client = AsyncClient("https://api.mainnet-beta.solana.com", Confirmed)
    print("Connecting...")
    await client.is_connected()

    memcmp_opts = [
        MemcmpOpts(offset=124, bytes="CAf8jfgqhia5VNrEF4A7Y9VLD3numMq9DVSceq7cPhNY"),
    ]  # put the pubkey of the validator vote address here
    response = await client.get_program_accounts(
        STAKE_PROGRAM_ID, encoding="base64", data_size=200, memcmp_opts=memcmp_opts
    )
    for stake in response["result"]:
        print(stake)

    await client.close()


def get_transaction_count(public_key):
    headers = {"Content-Type": "application/json"}
    offset = 0
    limit = 1000  # Fetch 1000 transactions per request
    total_transactions = 0

    RPC_URL: str = os.getenv("RPC_URL", "https://api2.pythnet.pyth.network")

    while True:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [public_key, {"limit": limit, "before": None if offset == 0 else offset}],
        }

        response = requests.post(RPC_URL, json=payload, headers=headers, timeout=15)
        if response.status_code != 200:
            raise Exception(f"Error fetching transactions: {response.text}")

        data = response.json()
        if "error" in data:
            raise Exception(f"Error in response: {data['error']}")

        # Extract transaction signatures
        signatures = data["result"]

        # Update the total count
        total_transactions += len(signatures)
        print(f"# of txes: {total_transactions}")
        print("-------------------------------")
        if len(signatures) < limit:
            # No more transactions to fetch
            break

        # Update the offset for the next batch
        offset = signatures[-1]["signature"]

    return total_transactions


if __name__ == "__main__":
    address = input("Enter the public address: ").strip()
    try:
        count = get_transaction_count(address)
        print(f"Total number of transactions for {address}: {count}")
    except Exception as e:
        print(f"An error occurred: {e}")
