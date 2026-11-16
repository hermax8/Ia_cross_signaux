#!/usr/bin/env python3
"""IA+CROSS V3 - signaux Telegram hors TradingView.

Refait le calcul de l'indicateur TradingView "3. IA+CROSS V3" sur les bougies
4h de Binance et envoie un message Telegram a chaque signal :
  VENTE (entree short du ratio alts/BTC), ACHAT (sortie), STOP.
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

# ---------------- Reglages (identiques a l'indicateur V3) ----------------
BASES = ["DOTUSDT", "ATOMUSDT", "XLMUSDT", "UNIUSDT", "CRVUSDT",
         "STXUSDT", "MANAUSDT", "SHIBUSDT", "FLOKIUSDT", "BONKUSDT"]
QUOTE = "BTCUSDT"
INTERVAL_MIN = 240        # bougies 4h
# Données de marché publiques de Binance (sans compte ni clé)
API = "https://data-api.binance.vision/api/v3/klines"
Z_LEN = 20
OUTER_UPPER = 2.0
INNER_UPPER = 1.2
OUTER_LOWER = -2.5
INNER_LOWER = -1.7
TREND_LEN = 200
STOP_PCT = 12.0
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


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


def compute(base_closes, quote_closes):
    """Rejoue la logique V3. Renvoie la liste des bougies avec ratio, z, evenements."""
    times = sorted(quote_closes)
    last = {}
    last_q = None
    idx_b = idx_q = 1.0
    ratio = []
    for t in times:
        s, n = 0.0, 0
        for sym, closes in base_closes.items():
            c = closes.get(t)
            if c is not None:
                if sym in last:
                    s += c / last[sym] - 1
                last[sym] = c
            n += 1
        q = quote_closes[t]
        rq = q / last_q - 1 if last_q else 0.0
        last_q = q
        idx_b *= 1 + (s / n if n else 0.0)
        idx_q *= 1 + rq
        ratio.append(idx_b / idx_q)

    bars = []
    upper_armed = lower_armed = False
    in_short = False
    entry = None
    prev_z = None
    for i, t in enumerate(times):
        z = trend = None
        if i >= Z_LEN - 1:
            w = ratio[i - Z_LEN + 1:i + 1]
            m = sum(w) / Z_LEN
            sd = math.sqrt(max(sum((x - m) ** 2 for x in w) / Z_LEN, 0.0))
            z = (ratio[i] - m) / sd if sd > 0 else None
        if i >= TREND_LEN - 1:
            trend = sum(ratio[i - TREND_LEN + 1:i + 1]) / TREND_LEN
        event = None
        pnl = None
        if z is not None:
            if z >= OUTER_UPPER:
                upper_armed = True
            if z <= OUTER_LOWER:
                lower_armed = True
            cu = prev_z is not None and prev_z >= INNER_UPPER and z < INNER_UPPER
            co = prev_z is not None and prev_z <= INNER_LOWER and z > INNER_LOWER
            sell_raw = upper_armed and cu
            if sell_raw:
                upper_armed = False
            buy_raw = lower_armed and co
            if buy_raw:
                lower_armed = False
            if in_short:
                adverse = (ratio[i] - entry) / entry * 100
                if buy_raw or adverse >= STOP_PCT:
                    event = "ACHAT" if buy_raw else "STOP"
                    pnl = -adverse
                    in_short = False
                    entry = None
            # on attend que la moyenne de tendance existe avant toute entree
            if event is None and sell_raw and not in_short and trend is not None and ratio[i] < trend:
                event = "VENTE"
                in_short = True
                entry = ratio[i]
        prev_z = z
        bars.append({"t": t, "ratio": ratio[i], "z": z, "trend": trend,
                     "event": event, "pnl": pnl, "in_short": in_short, "entry": entry})
    return bars


def fmt_time(ts):
    return time.strftime("%d/%m/%Y %H:%M UTC", time.gmtime(ts + INTERVAL_MIN * 60))


def message_for(bar):
    head = {
        "VENTE": "🔴 VENTE (entrée) — IA+CROSS V3\nShorter le panier d'alts / acheter BTC.",
        "ACHAT": "🟢 ACHAT (sortie) — IA+CROSS V3\nClôturer la position.",
        "STOP": "⛔ STOP — IA+CROSS V3\nPerte maximale atteinte : clôturer la position.",
    }[bar["event"]]
    lines = [head, f"Bougie 4h clôturée le {fmt_time(bar['t'])}",
             f"Ratio : {bar['ratio']:.6f}   Z-score : {bar['z']:.2f}"]
    if bar["pnl"] is not None:
        lines.append(f"Résultat du trade (hors frais) : {bar['pnl']:+.2f} %")
    return "\n".join(lines)


def status_message(bars):
    b = bars[-1]
    pos = "position short OUVERTE" if b["in_short"] else "aucune position"
    lines = ["ℹ️ IA+CROSS V3 — état actuel",
             f"Dernière bougie 4h clôturée : {fmt_time(b['t'])}",
             f"Ratio : {b['ratio']:.6f}   Z-score : {b['z']:.2f}",
             f"Moyenne {TREND_LEN} : {b['trend']:.6f}   État : {pos}"]
    if b["in_short"]:
        lines.append(f"Entrée à {b['entry']:.6f} ({(b['entry'] - b['ratio']) / b['entry'] * 100:+.2f} % en cours)")
    recent = [x for x in bars if x["event"]][-4:]
    if recent:
        lines.append("Derniers signaux :")
        lines += [f"  {fmt_time(x['t'])} — {x['event']}" for x in recent]
    return "\n".join(lines)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[pas de secrets Telegram : message non envoyé]\n" + text)
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def main():
    want_status = "--status" in sys.argv
    base = {}
    for sym in BASES:
        base[sym] = fetch_closes(sym)
        time.sleep(0.3)
    quote = fetch_closes(QUOTE)
    bars = compute(base, quote)
    if len(bars) < TREND_LEN + Z_LEN or bars[-1]["z"] is None:
        raise RuntimeError("Historique Binance insuffisant")

    state = {"last_notified": 0}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state.update(json.load(f))
    first_run = state["last_notified"] == 0
    # premier lancement : on ne renvoie pas les anciens signaux
    since = bars[-1]["t"] - 1 if first_run else state["last_notified"]
    for b in bars:
        if b["event"] and b["t"] > since:
            send_telegram(message_for(b))
            print("Signal envoyé :", b["event"], fmt_time(b["t"]))
    if want_status or first_run:
        send_telegram(status_message(bars))
    state["last_notified"] = bars[-1]["t"]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)
    print(status_message(bars))


if __name__ == "__main__":
    main()
