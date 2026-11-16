# Signaux IA+CROSS V3 sur Telegram

Refait le calcul de l'indicateur TradingView « 3. IA+CROSS V3 » sur les bougies 4h de Binance
et envoie un message Telegram à chaque signal : VENTE (entrée), ACHAT (sortie), STOP.
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

En haut de `signal.py` : liste des cryptos, seuils du Z-score, filtre de tendance, stop.
Ils doivent rester identiques à ceux de l'indicateur TradingView.

## Limites

- Les planifications GitHub peuvent avoir quelques minutes de retard ; un second passage de secours est prévu 30 minutes après.
- GitHub désactive les planifications d'un dépôt sans activité depuis 60 jours ; le fichier `state.json`
  est mis à jour à chaque nouvelle bougie, ce qui garde le dépôt actif.
- Le script recalcule tout sur les 1000 dernières bougies 4h (environ 166 jours) fournies par Binance.
