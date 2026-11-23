"""Passage d'ordres sur Kraken Futures pour les signaux IA+CROSS.

Modes (variable GitHub TRADING_MODE) :
  (vide)      : signaux Telegram seulement, aucun ordre
  simulation  : calcule les ordres et les envoie sur Telegram, sans rien passer
  reel        : passe vraiment les ordres sur Kraken Futures

Le bot ne touche qu'aux positions qu'il a ouvertes lui-même : il mémorise dans
state.json ce qu'il détient ("applied") et n'envoie que la différence avec la
cible. Une position ouverte à la main sur Kraken n'est donc jamais fermée par lui.
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

BASE = "https://futures.kraken.com/derivatives"
MAX_LEVIER = 10

# Binance (calcul des signaux) -> contrat perpétuel Kraken
def kraken_symbol(binance_pair):
    base = binance_pair.replace("USDT", "")
    return "PF_XBTUSD" if base == "BTC" else f"PF_{base}USD"


def short_name(symbol):
    s = symbol.replace("PF_", "").replace("USD", "")
    return "BTC" if s == "XBT" else s


# ---------------------------------------------------------------- réglages

def settings():
    def num(name, default):
        v = (os.environ.get(name) or "").strip().replace(",", ".")
        return float(v) if v else default
    mode = (os.environ.get("TRADING_MODE") or "").strip().lower()
    mode = {"réel": "reel", "real": "reel", "live": "reel", "sim": "simulation"}.get(mode, mode)
    levier = num("LEVIER", 3.0)
    if not 0 < levier <= MAX_LEVIER:
        raise RuntimeError(f"LEVIER doit être entre 1 et {MAX_LEVIER} (reçu {levier})")
    parts = {"BTC/TOP10": num("PART_TOP10", 0.7), "BTC/DOT": num("PART_DOT", 0.3)}
    if sum(parts.values()) > 1.0001 or min(parts.values()) < 0:
        raise RuntimeError("PART_TOP10 + PART_DOT doit être entre 0 et 1")
    return {"mode": mode if mode in ("simulation", "reel") else "",
            "levier": levier, "parts": parts,
            "capital_sim": num("CAPITAL_SIMULATION", 1000.0)}


# ---------------------------------------------------------------- API Kraken

def _request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    req.add_header("User-Agent", "ia-cross-signaux")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Kraken HTTP {e.code} : {e.read()[:300]!r}")


def public(path):
    return _request(BASE + path)


def sign(path, post_data, nonce, secret):
    """Signature Kraken Futures : HMAC-SHA512(base64 secret, SHA256(post + nonce + path))."""
    digest = hashlib.sha256((post_data + nonce + path).encode()).digest()
    mac = hmac.new(base64.b64decode(secret), digest, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def private(method, path, params=None):
    key = os.environ.get("KRAKEN_API_KEY")
    secret = os.environ.get("KRAKEN_API_SECRET")
    if not key or not secret:
        raise RuntimeError("Secrets KRAKEN_API_KEY / KRAKEN_API_SECRET manquants")
    post = urllib.parse.urlencode(params or {})
    nonce = str(int(time.time() * 1000))
    headers = {"APIKey": key, "Nonce": nonce, "Authent": sign(path, post, nonce, secret)}
    url = BASE + path
    if method == "GET":
        res = _request(url + ("?" + post if post else ""), headers=headers)
    else:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        res = _request(url, data=post.encode(), headers=headers, method=method)
    if res.get("result") != "success":
        raise RuntimeError(f"Kraken {path} : {res.get('error') or res}")
    return res


def market_data():
    """{symbole: prix mark} et {symbole: précision de taille} pour les PF_."""
    tick = public("/api/v3/tickers")["tickers"]
    prices = {t["symbol"]: float(t["markPrice"]) for t in tick
              if t.get("symbol", "").startswith("PF_") and t.get("markPrice")}
    inst = public("/api/v3/instruments")["instruments"]
    prec = {i["symbol"]: int(i.get("contractValueTradePrecision", 0)) for i in inst
            if i.get("symbol", "").startswith("PF_")}
    return prices, prec


def account_value():
    acc = private("GET", "/api/v3/accounts")["accounts"]
    flex = acc.get("flex") or {}
    v = flex.get("portfolioValue")
    if v is None:
        raise RuntimeError("Compte multi-collatéral (flex) introuvable sur Kraken Futures")
    return float(v)


def open_positions():
    res = private("GET", "/api/v3/openpositions")
    out = {}
    for p in res.get("openPositions", []):
        size = float(p["size"]) * (1 if p["side"] == "long" else -1)
        out[p["symbol"]] = out.get(p["symbol"], 0.0) + size
    return out


def send_order(symbol, size, reduce_only):
    params = {"orderType": "mkt", "symbol": symbol,
              "side": "buy" if size > 0 else "sell",
              "size": ("%.10f" % abs(size)).rstrip("0").rstrip(".")}
    if reduce_only:
        params["reduceOnly"] = "true"
    res = private("POST", "/api/v3/sendorder", params)
    status = (res.get("sendStatus") or {}).get("status", "")
    return status


# ---------------------------------------------------------------- calculs

def round_size(size, precision):
    r = round(size, precision)
    return float(r) if precision > 0 else float(int(r))


def entry_sizes(strat, capital, levier, prices, prec):
    """Tailles signées à l'entrée : moitié achat BTC, moitié vente des alts (parts égales)."""
    expo = capital * levier / 2
    sizes = {}
    btc = "PF_XBTUSD"
    sizes[btc] = round_size(expo / prices[btc], prec.get(btc, 4))
    alts = [kraken_symbol(b) for b in strat["bases"]]
    for sym in alts:
        if sym not in prices:
            raise RuntimeError(f"{sym} absent des futures Kraken")
        sizes[sym] = -round_size(expo / len(alts) / prices[sym], prec.get(sym, 0))
    return {k: v for k, v in sizes.items() if v}


def targets(state):
    tot = {}
    for sizes in state.get("positions", {}).values():
        for sym, s in sizes.items():
            tot[sym] = tot.get(sym, 0.0) + s
    return tot


def fmt_qty(q):
    txt = f"{abs(q):,.8f}".rstrip("0").rstrip(".")
    return txt.replace(",", " ")


def describe(sym, size, price):
    verb = "Acheter" if size > 0 else "Vendre"
    usd = f"{abs(size) * price:,.0f}".replace(",", " ")
    return f"{verb} {fmt_qty(size)} {short_name(sym)} (~{usd} $)"


# ---------------------------------------------------------------- point d'entrée

def check(send):
    """Test de connexion, sans aucun ordre : solde et positions ouvertes."""
    cfg = settings()
    lines = [f"🔌 Test Kraken (mode : {cfg['mode'] or 'signaux seulement'}, levier x{cfg['levier']:g})"]
    try:
        value = account_value()
        pos = open_positions()
        lines.append(f"Connexion OK. Valeur du compte futures : {value:,.2f} $".replace(",", " "))
        if pos:
            lines.append("Positions ouvertes : " + ", ".join(
                f"{short_name(s)} {v:+g}" for s, v in sorted(pos.items())))
        else:
            lines.append("Aucune position ouverte.")
    except Exception as e:
        lines.append(f"❌ Échec : {e}")
        send("\n".join(lines))
        raise
    send("\n".join(lines))


def handle(state, changes, strategies, send):
    """changes : {nom de stratégie: 'IN' ou 'OUT'} pour les nouveaux signaux de ce passage."""
    cfg = settings()
    if not cfg["mode"]:
        return
    sim = cfg["mode"] == "simulation"
    if state.get("mode") != cfg["mode"]:
        if cfg["mode"] == "reel" and (state.get("positions") or state.get("applied")):
            # positions fictives de la simulation : on ne les reporte pas sur le vrai compte
            send("🔁 Passage en réel : les positions de la simulation sont oubliées. "
                 "Le bot ouvrira ses premiers vrais ordres au prochain signal IN.")
        if cfg["mode"] == "reel":
            state["positions"], state["applied"] = {}, {}
        state["mode"] = cfg["mode"]
    by_name = {s["name"]: s for s in strategies}
    positions = state.setdefault("positions", {})
    applied = state.setdefault("applied", {})
    pending = any(abs(targets(state).get(s, 0.0) - applied.get(s, 0.0)) > 1e-12
                  for s in set(targets(state)) | set(applied))
    if not changes and not pending:
        return

    prices, prec = market_data()
    equity = None
    for name, ev in changes.items():
        if ev == "IN" and name not in positions:
            if equity is None:
                equity = cfg["capital_sim"] if sim else account_value()
            capital = equity * cfg["parts"].get(name, 0.0)
            if capital <= 0:
                continue
            positions[name] = entry_sizes(by_name[name], capital, cfg["levier"], prices, prec)
        elif ev == "OUT":
            positions.pop(name, None)

    tgt = targets(state)
    orders = []
    for sym in sorted(set(tgt) | set(applied)):
        diff = round_size(tgt.get(sym, 0.0) - applied.get(sym, 0.0), prec.get(sym, 4))
        if diff:
            held = applied.get(sym, 0.0)
            reduce_only = held != 0 and diff * held < 0 and abs(diff) <= abs(held) + 1e-12
            orders.append((sym, diff, reduce_only))

    if not orders:
        return
    # d'abord les fermetures (libèrent de la marge), BTC en tête, puis les alts
    orders.sort(key=lambda o: (not o[2], o[0] != "PF_XBTUSD", o[0]))
    tag = "🧪 Simulation" if sim else "🤖 Ordres Kraken"
    lines = [f"{tag} ({', '.join(f'{ev} {n}' for n, ev in changes.items()) or 'reprise'}, levier x{cfg['levier']:g})"]
    errors = []
    for sym, diff, reduce_only in orders:
        txt = describe(sym, diff, prices.get(sym, 0.0))
        if sim:
            applied[sym] = applied.get(sym, 0.0) + diff
            lines.append("• " + txt)
            continue
        try:
            status = send_order(sym, diff, reduce_only)
        except Exception as e:
            errors.append(sym)
            lines.append(f"❌ {txt} : {e}")
            continue
        if status == "placed":
            applied[sym] = applied.get(sym, 0.0) + diff
            lines.append("✅ " + txt)
        elif reduce_only and status == "wouldNotReducePosition":
            # position déjà fermée (ex. liquidation) : on ne rouvre rien en sens inverse
            applied[sym] = tgt.get(sym, 0.0)
            lines.append(f"⚠️ {txt} : position déjà fermée sur Kraken")
        else:
            errors.append(sym)
            lines.append(f"❌ {txt} : refusé ({status})")
    for sym in [s for s, v in applied.items() if abs(v) < 1e-12]:
        del applied[sym]
    if errors:
        lines.append("Les ordres en échec seront retentés au prochain passage (dans 30 min à 4 h).")
    send("\n".join(lines))
