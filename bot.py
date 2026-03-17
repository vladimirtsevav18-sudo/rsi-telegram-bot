import requests
import time
import pandas as pd
import os
from datetime import datetime

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text
    })


# ✅ сообщение при запуске
send_message("🚀 Бот запустился и работает")


def get_symbols():
    url = "https://api.binance.com/api/v3/exchangeInfo"

    try:
        response = requests.get(url, timeout=10)
        data = response.json()

        if "symbols" not in data:
            print("Ошибка Binance:", data)
            return []

        symbols = []

        for s in data["symbols"]:
            if s["quoteAsset"] == "USDT" and s["status"] == "TRADING":
                symbols.append(s["symbol"])

        return symbols

    except Exception as e:
        print("Ошибка получения списка монет:", e)
        return []


def get_rsi(symbol):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=100"
        data = requests.get(url, timeout=10).json()

        closes = [float(i[4]) for i in data]

        df = pd.Series(closes)

        delta = df.diff()

        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()

        rs = avg_gain / avg_loss

        rsi = 100 - (100 / (1 + rs))

        return rsi.iloc[-2]  # закрытая свеча

    except Exception as e:
        print(f"Ошибка RSI {symbol}:", e)
        return None


def scan():

    print("Сканируем рынок...")
    send_message("🔄 Бот делает скан рынка")

    symbols = get_symbols()

    if not symbols:
        send_message("⚠️ Не удалось получить список монет")
        return

    oversold = []
    overbought = []

    for symbol in symbols:

        rsi = get_rsi(symbol)

        if rsi is None:
            continue

        if rsi <= 30:
            oversold.append(f"{symbol} ({round(rsi,1)})")

        if rsi >= 70:
            overbought.append(f"{symbol} ({round(rsi,1)})")

        time.sleep(0.1)

    message = "📊 RSI сканер рынка\n\n"

    if oversold:
        message += "🔵 Перепроданность\n"
        message += "\n".join(oversold[:20])
        message += "\n\n"

    if overbought:
        message += "🔴 Перекупленность\n"
        message += "\n".join(overbought[:20])

    if not oversold and not overbought:
        message += "❗️Сигналов нет"

    send_message(message)


while True:

    try:

        now = datetime.utcnow()

        if now.minute == 0:
            scan()
            time.sleep(60)

        time.sleep(20)

    except Exception as e:
        print("Глобальная ошибка:", e)
        send_message("❌ Ошибка в работе бота")
        time.sleep(60)
