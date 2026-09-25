# Déploiement des bots — guide pas à pas

Ces scripts sont écrits pour tourner sur **ton propre serveur (VPS)**, pas sur cette machine de conversation. Je ne peux pas les héberger ni les exécuter pour toi en continu.

## 1. Avant tout : sécuriser la clé API Binance

Sur binance.com → Gestion API :
- Crée une clé **dédiée à ce bot** (jamais ta clé principale si tu en as une autre).
- Active uniquement **"Enable Spot & Margin Trading"**.
- **Désactive "Enable Withdrawals"** — non négociable. Même en cas de piratage du VPS, personne ne pourra sortir tes fonds.
- Restreins la clé à l'IP fixe de ton VPS (pas "aucune restriction").

## 2. Tester d'abord sur le Testnet Binance

Avant de brancher de l'argent réel, teste sur https://testnet.binance.vision (compte de test avec fonds fictifs). Change juste l'URL de base du client :
```python
client = Client(api_key, api_secret, testnet=True)
```

## 3. Choisir un VPS

Quelques options économiques (quelques euros/mois) : Contabo, Hetzner, OVH. Prends la plus petite instance Ubuntu — ces bots ne consomment presque rien.

## 4. Installer les dépendances sur le VPS

```bash
sudo apt update && sudo apt install python3-pip -y
pip3 install python-binance
```

## 5. Configurer les clés (jamais en dur dans le code)

```bash
export BINANCE_API_KEY="ta_cle"
export BINANCE_API_SECRET="ton_secret"
```
Ou mieux, dans un fichier `.env` chargé au démarrage, non commité nulle part.

## 6. Lancer les bots

**Grid trading** (tourne en continu) :
```bash
nohup python3 grid_trading_bot.py &
```
Ou, plus robuste, comme service systemd qui redémarre seul en cas de coupure.

**DCA renforcé** (une fois par semaine) — via cron, ex. tous les dimanches à 9h :
```bash
crontab -e
# ajouter :
0 9 * * 0 cd /chemin/vers/bots && /usr/bin/python3 dca_renforce_bot.py
```

## 7. Mode simulation d'abord

Les deux scripts démarrent en `dry_run=True` par défaut (voir `risk_guard.py`) : aucun ordre réel n'est passé, tout est journalisé dans `grid_bot.log` / `dca_bot.log`. Laisse tourner en simulation au moins 2 à 3 semaines, relis les logs, ajuste les paramètres (fourchette de prix, seuils) avant de passer en réel.

## 8. Ce qui est déjà verrouillé dans le code (risk_guard.py)

- Maximum 2 % du capital par ordre.
- Coupure automatique si -5 % sur une semaine glissante.
- 30 % du capital toujours gardé en réserve, jamais investi.

Ce sont des points de départ raisonnables, pas des valeurs figées — tu peux les ajuster dans `RiskConfig`, mais je te recommande de ne pas descendre la réserve sous 20 % ni monter le risque par trade au-dessus de 5 %.

## Limites à garder en tête

- Ces bots ne "prédisent" rien : le grid trading profite des oscillations, le DCA renforcé lisse ton coût d'entrée. Aucun des deux ne garantit un gain.
- Un bot mal configuré (mauvaise fourchette de prix pour le grid trading, par exemple) peut sous-performer un simple DCA manuel.
- Ni moi ni ce code ne remplaçons un suivi actif de ta part — relis les logs régulièrement.
