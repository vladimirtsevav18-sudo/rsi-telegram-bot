import requests
import time
import pandas as pd
import os
from datetime import datetime

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

symbols = [
"BTCUSDT",
"ETHUSDT",
"SOLUSDT",
"BNBUSDT",
"XRPUSDT",
"ADAUSDT",
"AVAXUSDT",
"DOGEUSDT",
"LINKUSDT",
"TONUSDT"
]

def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url,data={
        "chat_id":CHAT_ID,
        "text":text
    })

def get_rsi(symbol):

    url=f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=100"

    data=requests.get(url).json()

    closes=[float(i[4]) for i in data]

    df=pd.Series(closes)

    delta=df.diff()

    gain=delta.clip(lower=0)
    loss=-delta.clip(upper=0)

    avg_gain=gain.rolling(14).mean()
    avg_loss=loss.rolling(14).mean()

    rs=avg_gain/avg_loss

    rsi=100-(100/(1+rs))

    return rsi.iloc[-2]

def check():

    for symbol in symbols:

        rsi=get_rsi(symbol)

        if rsi>=70:

            send_message(
f"""🚨 RSI сигнал

{symbol}
TF 1H

RSI {round(rsi,2)}

Перекупленность
Сигнал по закрытию свечи"""
)

        if rsi<=30:

            send_message(
f"""🚨 RSI сигнал

{symbol}
TF 1H

RSI {round(rsi,2)}

Перепроданность
Сигнал по закрытию свечи"""
)

while True:

    now=datetime.utcnow()

    if now.minute==0:

        check()

        time.sleep(60)

    time.sleep(20)
