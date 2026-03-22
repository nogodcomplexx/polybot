import asyncio
from src.api.gamma_client import GammaClient

async def test_parse():
    client = GammaClient()
    # Mock some raw data with JSON string tokens
    raw = {
        "conditionId": "0x123",
        "question": "Test?",
        "clobTokenIds": '["TOKEN_YES", "TOKEN_NO"]',
        "outcomePrices": '["0.5", "0.5"]'
    }
    market = client.parse_market(raw)
    print(f"YES Token: {market.yes_token_id}")
    print(f"NO Token: {market.no_token_id}")
    print(f"YES Price: {market.yes_price}")
    
    # Test with real list
    raw2 = {
        "conditionId": "0x456",
        "question": "Test 2?",
        "clobTokenIds": ["REAL_YES", "REAL_NO"],
        "outcomePrices": ["0.6", "0.4"]
    }
    market2 = client.parse_market(raw2)
    print(f"\nYES Token 2: {market2.yes_token_id}")
    print(f"NO Token 2: {market2.no_token_id}")
    
    await client.close()

if __name__ == "__main__":
    asyncio.run(test_parse())
