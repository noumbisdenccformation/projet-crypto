"""
dca_renforce_bot.py
=====================
Bot de DCA renforcé : achète un montant de base chaque semaine, mais
augmente le montant si le prix a bien baissé récemment, et le réduit s'il a
monté. C'est une version automatisée et disciplinée de ce que tu fais déjà
à la main chaque week-end.

À DÉPLOYER SUR TON PROPRE SERVEUR (VPS), PAS SUR CETTE MACHINE.
Prévu pour être lancé une fois par semaine (ex. via une tâche cron le
dimanche matin), pas en boucle continue comme le bot de grid trading.

Prérequis :
    pip install python-binance

Variables d'environnement requises :
    BINANCE_API_KEY
    BINANCE_API_SECRET
"""

import os
import logging

from binance.client import Client  # pip install python-binance

from risk_guard import RiskGuard, RiskConfig

# ----------------------------------------------------------------------
# Configuration de la stratégie — À AJUSTER avant tout lancement
# ----------------------------------------------------------------------

SYMBOLS = ["BTCEUR", "ETHEUR", "SOLEUR", "BNBEUR"]  # répartition égale entre ces 4
BASE_AMOUNT_EUR = 5.0          # montant de base par actif, par semaine
LOOKBACK_DAYS = 7              # période de comparaison pour mesurer la variation
DROP_THRESHOLD_PCT = -0.08     # si le prix a baissé de 8%+ -> on renforce
RISE_THRESHOLD_PCT = 0.08      # si le prix a monté de 8%+ -> on réduit
MULTIPLIER_ON_DROP = 1.5       # achète 1.5x le montant de base si forte baisse
MULTIPLIER_ON_RISE = 0.5       # achète 0.5x le montant de base si forte hausse

STARTING_CAPITAL_EUR = 50.0

logging.basicConfig(
    filename="dca_bot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("dca_bot")


class DcaRenforceBot:
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
            config=RiskConfig(state_path="dca_risk_state.json"),
            starting_capital_eur=STARTING_CAPITAL_EUR,
        )

    def get_current_price(self, symbol: str) -> float:
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def get_price_n_days_ago(self, symbol: str, days: int) -> float:
        klines = self.client.get_historical_klines(
            symbol, Client.KLINE_INTERVAL_1DAY, f"{days} day ago UTC"
        )
        return float(klines[0][1])  # prix d'ouverture il y a `days` jours

    def get_stable_balance_eur(self) -> float:
        balance = self.client.get_asset_balance(asset="EUR")
        return float(balance["free"]) if balance else 0.0

    def compute_amount(self, symbol: str) -> float:
        current = self.get_current_price(symbol)
        past = self.get_price_n_days_ago(symbol, LOOKBACK_DAYS)
        variation_pct = (current - past) / past

        if variation_pct <= DROP_THRESHOLD_PCT:
            multiplier = MULTIPLIER_ON_DROP
            logger.info(f"{symbol} : baisse de {variation_pct:.1%} -> renforcement (x{multiplier})")
        elif variation_pct >= RISE_THRESHOLD_PCT:
            multiplier = MULTIPLIER_ON_RISE
            logger.info(f"{symbol} : hausse de {variation_pct:.1%} -> réduction (x{multiplier})")
        else:
            multiplier = 1.0
            logger.info(f"{symbol} : variation de {variation_pct:.1%} -> montant de base")

        return BASE_AMOUNT_EUR * multiplier

    def run_weekly_purchase(self):
        stable_balance = self.get_stable_balance_eur()
        logger.info(f"Solde EUR disponible avant achats : {stable_balance:.2f} €")

        for symbol in SYMBOLS:
            try:
                amount_eur = self.compute_amount(symbol)
            except Exception as e:
                logger.error(f"Erreur de calcul pour {symbol} : {e}")
                continue

            allowed, reason = self.risk.check_order(amount_eur, stable_balance)
            if not allowed:
                logger.warning(f"Achat {symbol} refusé : {reason}")
                continue

            price = self.get_current_price(symbol)
            quantity = round(amount_eur / price, 6)

            if self.risk.config.dry_run:
                logger.info(f"[SIMULATION] Achat {quantity} {symbol} (~{amount_eur:.2f} €) au prix {price}")
            else:
                order = self.client.order_market_buy(symbol=symbol, quoteOrderQty=amount_eur)
                logger.info(f"Ordre d'achat réel envoyé : {order}")

            stable_balance -= amount_eur


if __name__ == "__main__":
    bot = DcaRenforceBot()
    bot.run_weekly_purchase()
