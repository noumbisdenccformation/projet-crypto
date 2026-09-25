"""
grid_trading_bot.py
====================
Bot de grid trading : place des ordres d'achat/vente échelonnés sur une
fourchette de prix et profite des oscillations du marché, sans parier sur
une direction (hausse ou baisse).

À DÉPLOYER SUR TON PROPRE SERVEUR (VPS), PAS SUR CETTE MACHINE.
Ce script ne s'exécute pas ici — il est écrit pour que tu le copies sur un
VPS à toi (voir README_DEPLOIEMENT.md).

Prérequis :
    pip install python-binance

Variables d'environnement requises (jamais de clé en dur dans le code) :
    BINANCE_API_KEY
    BINANCE_API_SECRET

Sécurité de la clé API (à faire sur binance.com AVANT de lancer le bot) :
    - Droits activés : "Enable Spot & Margin Trading" uniquement
    - Droits désactivés : "Enable Withdrawals" (retrait) — TOUJOURS désactivé
    - Restriction IP : uniquement l'IP de ton VPS
"""

import os
import time
import logging
from datetime import datetime

from binance.client import Client  # pip install python-binance

from risk_guard import RiskGuard, RiskConfig

# ----------------------------------------------------------------------
# Configuration de la stratégie — À AJUSTER avant tout lancement
# ----------------------------------------------------------------------

SYMBOL = "BTCEUR"          # paire tradée
PRICE_LOW = 55000.0        # borne basse de la fourchette
PRICE_HIGH = 65000.0       # borne haute de la fourchette
GRID_LEVELS = 8            # nombre de paliers entre les deux bornes
POLL_INTERVAL_SECONDS = 60 # fréquence de vérification du marché

STARTING_CAPITAL_EUR = 50.0  # capital total que TU as décidé d'allouer à ce bot

logging.basicConfig(
    filename="grid_bot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("grid_bot")


def build_grid(low: float, high: float, levels: int) -> list[float]:
    step = (high - low) / levels
    return [round(low + i * step, 2) for i in range(levels + 1)]


class GridTradingBot:
    def __init__(self):
        api_key = os.environ.get("BINANCE_API_KEY")
        api_secret = os.environ.get("BINANCE_API_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError(
                "Clés API manquantes. Définis BINANCE_API_KEY et BINANCE_API_SECRET "
                "en variables d'environnement (jamais dans le code)."
            )

        self.client = Client(api_key, api_secret)
        self.risk = RiskGuard(
            config=RiskConfig(state_path="grid_risk_state.json"),
            starting_capital_eur=STARTING_CAPITAL_EUR,
        )
        self.grid = build_grid(PRICE_LOW, PRICE_HIGH, GRID_LEVELS)
        self.open_buy_levels = set(self.grid[:-1])  # niveaux où on peut encore acheter
        logger.info(f"Bot initialisé. Grille : {self.grid}")

    def get_current_price(self) -> float:
        ticker = self.client.get_symbol_ticker(symbol=SYMBOL)
        return float(ticker["price"])

    def get_stable_balance_eur(self) -> float:
        """Solde disponible en stablecoin (à adapter selon ta devise de base)."""
        balance = self.client.get_asset_balance(asset="EUR")
        return float(balance["free"]) if balance else 0.0

    def place_buy(self, level_price: float):
        amount_eur = self.risk.max_order_size_eur()
        stable_balance = self.get_stable_balance_eur()

        allowed, reason = self.risk.check_order(amount_eur, stable_balance)
        if not allowed:
            logger.warning(f"Achat refusé au niveau {level_price} : {reason}")
            return

        quantity = round(amount_eur / level_price, 6)

        if self.risk.config.dry_run:
            logger.info(f"[SIMULATION] Achat {quantity} {SYMBOL} au niveau {level_price} (~{amount_eur:.2f} €)")
        else:
            order = self.client.order_limit_buy(
                symbol=SYMBOL,
                quantity=quantity,
                price=str(level_price),
            )
            logger.info(f"Ordre d'achat réel envoyé : {order}")

        self.open_buy_levels.discard(level_price)

    def place_sell(self, level_price: float, quantity: float, buy_price: float):
        if self.risk.config.dry_run:
            pnl = (level_price - buy_price) * quantity
            logger.info(f"[SIMULATION] Vente {quantity} {SYMBOL} au niveau {level_price} (P&L estimé : {pnl:.2f} €)")
            self.risk.record_trade_result(pnl)
        else:
            order = self.client.order_limit_sell(
                symbol=SYMBOL,
                quantity=quantity,
                price=str(level_price),
            )
            logger.info(f"Ordre de vente réel envoyé : {order}")

    def run_once(self):
        try:
            price = self.get_current_price()
        except Exception as e:
            logger.error(f"Erreur de récupération du prix : {e}")
            return

        logger.info(f"Prix actuel {SYMBOL} : {price}")

        # Achète si le prix touche un niveau de grille encore disponible
        for level in sorted(self.open_buy_levels):
            if abs(price - level) / level < 0.002:  # tolérance 0.2%
                self.place_buy(level)

    def run_forever(self):
        logger.info("Démarrage du bot de grid trading (Ctrl+C pour arrêter).")
        while True:
            self.run_once()
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    bot = GridTradingBot()
    bot.run_forever()
