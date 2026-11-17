# Signaux IA+CROSS V3 sur Telegram

Refait le calcul de l'indicateur TradingView « 3. IA+CROSS V3 » sur les bougies 4h de Binance,
pour deux stratégies, et envoie un message Telegram à chaque signal :

| Message | Ce qu'il faut faire |
|---|---|
| 🔴 IN BTC/TOP10 | Acheter BTC et vendre le panier des 10 alts |
| 🟢 OUT BTC/TOP10 | Revendre BTC et racheter le panier |
| 🔴 IN BTC/DOT | Acheter BTC et vendre DOT |
| 🟢 OUT BTC/DOT | Revendre BTC et racheter DOT |
| ⛔ OUT … (stop) | Sortie forcée : perte maximale atteinte |
Tourne tout seul sur GitHub Actions, quelques minutes après chaque clôture de bougie 4h.

## Installation

1. **Créer le bot Telegram.** Dans Telegram, ouvrir `@BotFather`, envoyer `/newbot`, choisir un nom.
   BotFather donne un *token* (de la forme `123456:ABC...`).
2. **Récupérer ton identifiant de chat.** Envoyer un message quelconque à ton nouveau bot, puis ouvrir
   `@userinfobot` et envoyer `/start` : il affiche ton `Id` (un nombre).
3. **Créer un dépôt GitHub privé** et y déposer ces fichiers (en gardant le dossier `.github/workflows/`).
4. **Ajouter les deux secrets** dans le dépôt : *Settings → Secrets and variables → Actions → New repository secret*
   - `TELEGRAM_BOT_TOKEN` : le token de BotFather
   - `TELEGRAM_CHAT_ID` : ton identifiant de chat
5. **Tester.** Onglet *Actions → Signaux IA+CROSS V3 → Run workflow*. Tu dois recevoir un message d'état
   (ratio, Z-score, position en cours, derniers signaux) à comparer avec ton graphique.

## Réglages

Dans `signal.py`, bloc `STRATEGIES` : cryptos, seuils du Z-score, filtre de tendance, stop, pour chaque stratégie.
Ils doivent rester identiques à ceux de l'indicateur TradingView.

## Limites

- Les planifications GitHub peuvent avoir quelques minutes de retard ; un second passage de secours est prévu 30 minutes après.
- GitHub désactive les planifications d'un dépôt sans activité depuis 60 jours ; le fichier `state.json`
  est mis à jour à chaque nouvelle bougie, ce qui garde le dépôt actif.
- Le script recalcule tout sur les 1000 dernières bougies 4h (environ 166 jours) fournies par Binance.
