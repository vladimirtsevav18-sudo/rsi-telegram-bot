import requests
import time
import pandas as pd
import os
from datetime import datetime

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


def send_message(text):

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    requests.post(url,data={
        "chat_id":CHAT_ID,
        "text":text
    })


def get_symbols():

    url="https://api.binance.com/api/v3/exchangeInfo"

    data=requests.get(url).json()

    symbols=[]

    for s in data["symbols"]:

        if s["quoteAsset"]=="USDT" and s["status"]=="TRADING":

            symbols.append(s["symbol"])

    return symbols


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


def scan():

    symbols=get_symbols()

    oversold=[]
    overbought=[]

    for symbol in symbols:

        try:

            rsi=get_rsi(symbol)

            if rsi<=30:

                oversold.append(f"{symbol} ({round(rsi,1)})")

            if rsi>=70:

                overbought.append(f"{symbol} ({round(rsi,1)})")

        except:

            pass


    message="📊 RSI сканер рынка\n\n"

    if oversold:

        message+="🔵 Перепроданность\n"
        message+="\n".join(oversold[:20])
        message+="\n\n"

    if overbought:

        message+="🔴 Перекупленность\n"
        message+="\n".join(overbought[:20])


    send_message(message)


while True:

    now=datetime.utcnow()

    if now.minute==0:

        scan()

        time.sleep(60)

    time.sleep(20)
