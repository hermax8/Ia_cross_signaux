#!/usr/bin/env python3
"""IA+CROSS - signaux Telegram hors TradingView.

Refait le calcul de l'indicateur TradingView "3. IA+CROSS V3" sur les bougies
4h de Binance, pour deux strategies, et envoie un message Telegram simple :

Les resultats sont en % du capital investi, reparti moitie en BTC achete et
moitie en alts vendues (ex. 100 EUR = 50 EUR de BTC + 50 EUR d'alts).

  IN  BTC/TOP10 : acheter BTC, vendre le panier de 10 alts
  OUT BTC/TOP10 : cloturer (revendre BTC, racheter le panier)
  IN  BTC/DOT   : acheter BTC, vendre DOT
  OUT BTC/DOT   : cloturer (revendre BTC, racheter DOT)
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

import trading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

INTERVAL_MIN = 240        # bougies 4h
QUOTE = "BTCUSDT"
# Données de marché publiques de Binance (sans compte ni clé)
API = "https://data-api.binance.vision/api/v3/klines"
LEVERAGES = [1, 5, 10]
LIQUIDATION = -90.0   # perte (en % du capital) a laquelle on considere le compte liquide
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# ---------------- Strategies (identiques aux reglages TradingView) ----------------
STRATEGIES = [
    {
        "name": "BTC/TOP10",
        "bases": ["DOTUSDT", "ATOMUSDT", "XLMUSDT", "UNIUSDT", "CRVUSDT",
                  "STXUSDT", "MANAUSDT", "SHIBUSDT", "FLOKIUSDT", "BONKUSDT"],
        "z_len": 20,
        "outer_upper": 2.0, "inner_upper": 1.2,
        "outer_lower": -2.5, "inner_lower": -1.7,
        "trend_len": 200,     # 0 = pas de filtre de tendance
        "stop_pct": 12.0,
    },
    {
        "name": "BTC/DOT",
        "bases": ["DOTUSDT"],
        "z_len": 20,
        "outer_upper": 2.0, "inner_upper": 2.0,
        "outer_lower": -3.0, "inner_lower": -3.0,
        "trend_len": 0,
        "stop_pct": 20.0,
    },
]


def fetch_closes(pair):
    """Bougies 4h Binance -> {timestamp: cloture}. La bougie en cours est exclue."""
    url = API + "?" + urllib.parse.urlencode({"symbol": pair, "interval": "4h", "limit": 1000})
    last_err = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ia-cross-signaux"})
            with urllib.request.urlopen(req, timeout=30) as r:
                rows = json.load(r)
            now_ms = time.time() * 1000
            # c[0] = ouverture (ms), c[4] = cloture, c[6] = fin de bougie (ms)
            return {int(c[0]) // 1000: float(c[4]) for c in rows if int(c[6]) < now_ms}
        except Exception as e:  # reseau / limite de debit : on reessaie
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Binance {pair}: {last_err}")


def compute(strat, closes, quote_closes):
    """Rejoue la logique de l'indicateur pour une strategie. Renvoie une liste de bougies."""
    times = sorted(quote_closes)
    last = {}
    last_q = None
    idx_b = idx_q = 1.0
    ratio = []
    prices = []   # (prix BTC, derniers prix des alts) a chaque bougie
    bases = strat["bases"]
    for t in times:
        s = 0.0
        for sym in bases:
            c = closes[sym].get(t)
            if c is not None:
                if sym in last:
                    s += c / last[sym] - 1
                last[sym] = c
        q = quote_closes[t]
        rq = q / last_q - 1 if last_q else 0.0
        last_q = q
        idx_b *= 1 + s / len(bases)
        idx_q *= 1 + rq
        ratio.append(idx_b / idx_q)
        prices.append((q, dict(last)))

    z_len, trend_len = strat["z_len"], strat["trend_len"]
    bars = []
    upper_armed = lower_armed = False
    in_pos = False
    entry = None
    entry_i = None
    worst = 0.0       # pire perte en cours de trade (x1, en % du capital, sur les clotures)
    prev_z = None

    def real_gain(i):
        """Gain en % du capital : moitie en BTC achete, moitie en alts vendues."""
        q0, alts0 = prices[entry_i]
        q1, alts1 = prices[i]
        b = q1 / q0 - 1
        a = sum(alts1[s] / alts0[s] - 1 for s in bases if s in alts0 and s in alts1) / len(bases)
        return (0.5 * b - 0.5 * a) * 100
    for i, t in enumerate(times):
        z = trend = None
        if i >= z_len - 1:
            w = ratio[i - z_len + 1:i + 1]
            m = sum(w) / z_len
            sd = math.sqrt(max(sum((x - m) ** 2 for x in w) / z_len, 0.0))
            z = (ratio[i] - m) / sd if sd > 0 else None
        if trend_len and i >= trend_len - 1:
            trend = sum(ratio[i - trend_len + 1:i + 1]) / trend_len
        event = None
        pnl = None
        trade_worst = None
        if z is not None:
            if z >= strat["outer_upper"]:
                upper_armed = True
            if z <= strat["outer_lower"]:
                lower_armed = True
            cu = prev_z is not None and prev_z >= strat["inner_upper"] and z < strat["inner_upper"]
            co = prev_z is not None and prev_z <= strat["inner_lower"] and z > strat["inner_lower"]
            sell_raw = upper_armed and cu
            if sell_raw:
                upper_armed = False
            buy_raw = lower_armed and co
            if buy_raw:
                lower_armed = False
            if in_pos:
                worst = min(worst, real_gain(i))
                adverse = (ratio[i] - entry) / entry * 100
                if buy_raw or adverse >= strat["stop_pct"]:
                    event = "OUT" if buy_raw else "STOP"
                    pnl = real_gain(i)
                    trade_worst = worst
                    in_pos = False
                    entry = None
                    entry_i = None
            trend_ok = trend_len == 0 or (trend is not None and ratio[i] < trend)
            # on attend assez d'historique (200 bougies) avant toute entree
            if event is None and sell_raw and not in_pos and trend_ok and i >= 200:
                event = "IN"
                in_pos = True
                entry = ratio[i]
                entry_i = i
                worst = 0.0
        prev_z = z
        bars.append({"t": t, "ratio": ratio[i], "z": z, "event": event, "pnl": pnl,
                     "in_pos": in_pos, "entry": entry,
                     "gain": real_gain(i) if in_pos else None,
                     "worst": trade_worst if event in ("OUT", "STOP") else (worst if in_pos else None)})
    return bars


def fmt_time(ts):
    """Heure de cloture de la bougie, en heure de Paris."""
    end = datetime.fromtimestamp(ts + INTERVAL_MIN * 60, timezone.utc)
    try:
        end = end.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:  # base de fuseaux absente : on reste en UTC
        return end.strftime("%d/%m à %Hh%M UTC")
    return end.strftime("%d/%m à %Hh%M")


def legs(strat):
    if len(strat["bases"]) == 1:
        alt = strat["bases"][0].replace("USDT", "")
        return f"BTC et vendre {alt}", f"BTC et racheter {alt}"
    return "BTC et vendre le panier TOP10", "BTC et racheter le panier TOP10"


def by_leverage(gain, worst):
    """'+3.2 % (x1) | +16.0 % (x5) | liquidé (x10)' : levier applique au resultat."""
    parts = []
    for lev in LEVERAGES:
        if worst is not None and lev * worst <= LIQUIDATION:
            parts.append(f"liquidé (x{lev})")
        else:
            parts.append(f"{lev * gain:+.1f} % (x{lev})")
    return " | ".join(parts)


def message_for(strat, bar):
    name = strat["name"]
    sell_alt, buy_alt = legs(strat)
    when = fmt_time(bar["t"])
    if bar["event"] == "IN":
        return f"🔴 IN {name}\nAcheter {sell_alt}.\nSignal du {when}"
    title = f"🟢 OUT {name}" if bar["event"] == "OUT" else f"⛔ OUT {name} (stop)"
    return (f"{title}\nRevendre {buy_alt}.\n"
            f"Résultat (hors frais) :\n{by_leverage(bar['pnl'], bar['worst'])}\nSignal du {when}")


def status_line(strat, bars):
    b = bars[-1]
    if b["in_pos"]:
        since = next(x for x in reversed(bars) if x["event"] == "IN")
        return (f"• {strat['name']} : IN depuis le {fmt_time(since['t'])}\n"
                f"   {by_leverage(b['gain'], b['worst'])}")
    last_out = next((x for x in reversed(bars) if x["event"] in ("OUT", "STOP")), None)
    tail = f" (dernier OUT le {fmt_time(last_out['t'])})" if last_out else ""
    return f"• {strat['name']} : OUT, en attente{tail}"


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        manquants = [n for n, v in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat)) if not v]
        if os.environ.get("GITHUB_ACTIONS"):
            # sur GitHub, un secret absent doit faire echouer le lancement (croix rouge)
            raise RuntimeError("Secret(s) GitHub manquant(s) ou mal nomme(s) : " + ", ".join(manquants))
        print("[pas de secrets Telegram : message non envoyé]\n" + text + "\n")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def main():
    if "--test-kraken" in sys.argv:
        trading.check(send_telegram)
        return
    want_status = "--status" in sys.argv
    symbols = sorted({s for st in STRATEGIES for s in st["bases"]})
    closes = {}
    for sym in symbols:
        closes[sym] = fetch_closes(sym)
        time.sleep(0.3)
    quote = fetch_closes(QUOTE)

    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
    # ancien format (une seule strategie) -> nouveau format par strategie
    if "last_notified" in state:
        state = {"BTC/TOP10": state["last_notified"]}

    lines = []
    changes = {}
    new_strategy = False
    for strat in STRATEGIES:
        bars = compute(strat, closes, quote)
        if len(bars) < 250 or bars[-1]["z"] is None:
            raise RuntimeError(f"Historique Binance insuffisant pour {strat['name']}")
        name = strat["name"]
        if name not in state:
            # strategie nouvelle : on ne renvoie pas les anciens signaux
            state[name] = bars[-1]["t"]
            new_strategy = True
        for b in bars:
            if b["event"] and b["t"] > state[name]:
                send_telegram(message_for(strat, b))
                print("Signal envoyé :", name, b["event"], fmt_time(b["t"]))
                changes[name] = "IN" if b["event"] == "IN" else "OUT"
        state[name] = bars[-1]["t"]
        lines.append(status_line(strat, bars))

    status = ("📊 État des signaux\n" + "\n".join(lines)
              + "\n(% du capital : moitié en BTC acheté, moitié en alts vendues, hors frais)")
    if want_status or new_strategy:
        send_telegram(status)
    try:
        trading.handle(state, changes, STRATEGIES, send_telegram)
    except Exception as e:
        send_telegram(f"❌ Erreur côté Kraken, aucun ordre de ce passage n'est garanti :\n{e}")
        raise
    finally:
        # on mémorise toujours, même si un ordre a échoué (il sera retenté)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    print(status)


if __name__ == "__main__":
    main()
