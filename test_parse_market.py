import asyncio

from src.api.gamma_client import GammaClient


CONDITIONS = [
    "0xb8d4254ba84a185d8da18ae64b85698e33f7b9ab686d5514f6708678ee5a4612",
    "0xc0ad3e2ad1d067f6e2ead9cc695d1a57596d6b240cefdcab77d9a27360c182f6",
]


async def main() -> None:
    client = GammaClient()
    for condition_id in CONDITIONS:
        raw = await client.get_market(condition_id)
        if not raw:
            print(f"No raw market for {condition_id}")
            continue
        m = client.parse_market(raw)
        print(f"{condition_id}")
        print("  yes_token_id:", m.yes_token_id, "len:", len(m.yes_token_id))
        print("  no_token_id:", m.no_token_id, "len:", len(m.no_token_id))
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())

