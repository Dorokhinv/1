import json
import os
import sys
import time
from decimal import Decimal
from urllib.parse import urlencode
from urllib.request import urlopen

os.environ["TELEGRAM_STATE_DIR"] = "/var/lib/crypto-news"
import telegram_delivery as tg

SYMBOLS = [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")]
PERIOD = 1800  # 30 минут


def price(symbol):
    url = "https://api.binance.com/api/v3/ticker/price?" + urlencode({"symbol": symbol})
    with urlopen(url, timeout=20) as response:
        value = Decimal(json.load(response)["price"])
    if not value.is_finite() or value <= 0:
        raise ValueError("Invalid Binance price")
    return value


def fmt(value):
    return f"{value:,.2f}".replace(",", " ")


def main():
    check = "--check" in sys.argv
    lines = [f"{name}: {fmt(price(symbol))} USDT" for symbol, name in SYMBOLS]
    text = "Binance, цены сейчас\n" + "\n".join(lines)
    print(text, flush=True)
    if check:
        return
    key = f"prices:{int(time.time() // PERIOD)}"
    if key in tg.read("delivery.json", {}):
        print("SKIP: уже отправлено в этом получасе", flush=True)
        return
    result = tg.send_digest({"text": text, "event_keys": [key]})
    if not result.get("sent"):
        raise RuntimeError("Delivery not confirmed")
    print("SENT", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERROR:", type(error).__name__, getattr(error, "code", ""))
        sys.exit(1)
