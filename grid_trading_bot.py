"""
grid_trading_bot.py
====================
Bot de grid trading multi-actifs (BTC, ETH, SOL, BNB contre USDT) : place
des ordres d'achat/vente échelonnés sur une fourchette de prix centrée sur
le prix courant de chaque actif, et profite des oscillations du marché,
sans parier sur une direction (hausse ou baisse).

ADAPTÉ POUR GITHUB ACTIONS : ce script s'exécute UNE FOIS par appel, puis
s'arrête — pas de boucle infinie. Le workflow .github/workflows/grid_bot.yml
le relance toutes les 15 minutes. L'état (grille de chaque actif, positions
ouvertes, P&L de la semaine) est sauvegardé dans des fichiers JSON
(grid_state.json, grid_risk_state.json) que le workflow committe dans le
dépôt après chaque exécution, pour qu'il survive d'un passage à l'autre.

Prérequis :
    pip install python-binance

Variables d'environnement requises (jamais de clé en dur dans le code) :
    BINANCE_API_KEY
    BINANCE_API_SECRET
    BINANCE_TESTNET=true   -> utilise le testnet Binance (recommandé au début)

Sécurité de la clé API (à faire sur binance.com AVANT de lancer le bot) :
    - Droits activés : "Enable Spot & Margin Trading" uniquement
    - Droits désactivés : "Enable Withdrawals" (retrait) — TOUJOURS désactivé
"""

import os
import json
import logging

from binance.client import Client  # pip install python-binance

from risk_guard import RiskGuard, RiskConfig

# ----------------------------------------------------------------------
# Configuration de la stratégie — À AJUSTER avant tout lancement
# ----------------------------------------------------------------------

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

# La grille est centrée sur le prix courant de chaque actif (pas besoin de
# connaître les prix à l'avance) et recalculée si le prix s'en éloigne trop.
GRID_WIDTH_PCT = 0.10          # la grille couvre ±10 % autour du prix de référence
GRID_LEVELS = 6                # nombre de paliers d'achat ET de paliers de vente
REBALANCE_OUTSIDE_PCT = 0.15   # si le prix sort de ±15 % du centre, on recentre la grille
TOUCH_TOLERANCE_PCT = 0.003    # tolérance pour considérer qu'un palier est "touché"

STARTING_CAPITAL_USDT = 150.0  # capital total alloué à ce bot, réparti sur les 4 actifs

STATE_PATH = "grid_state.json"
RISK_STATE_PATH = "grid_risk_state.json"

logging.basicConfig(
    filename="grid_bot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("grid_bot")


def build_grid(center: float, width_pct: float, levels: int) -> list[float]:
    """Construit une grille de `levels + 1` prix, centrée sur `center`."""
    low = center * (1 - width_pct)
    high = center * (1 + width_pct)
    step = (high - low) / levels
    return [round(low + i * step, 6) for i in range(levels + 1)]


class GridTradingBot:
    def __init__(self):
        api_key = os.environ.get("BINANCE_API_KEY")
        api_secret = os.environ.get("BINANCE_API_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError(
                "Clés API manquantes. Définis BINANCE_API_KEY et BINANCE_API_SECRET "
                "en variables d'environnement (jamais dans le code)."
            )
        use_testnet = os.environ.get("BINANCE_TESTNET", "true").lower() == "true"
        self.client = Client(api_key, api_secret, testnet=use_testnet)
        logger.info(f"Client Binance initialisé (testnet={use_testnet}).")

        self.risk = RiskGuard(
            config=RiskConfig(state_path=RISK_STATE_PATH),
            starting_capital=STARTING_CAPITAL_USDT,
        )
        self.state = self._load_state()

    # ---- Persistance de l'état des grilles (positions ouvertes par actif) ----

    def _load_state(self) -> dict:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as f:
                return json.load(f)
        return {symbol: {"center": None, "grid": [], "positions": {}} for symbol in SYMBOLS}

    def _save_state(self):
        with open(STATE_PATH, "w") as f:
            json.dump(self.state, f, indent=2)

    # ---- Accès marché / solde ----

    def get_current_price(self, symbol: str) -> float:
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def get_quote_balance(self) -> float:
        """Solde disponible en USDT (réserve commune aux 4 actifs)."""
        balance = self.client.get_asset_balance(asset="USDT")
        return float(balance["free"]) if balance else 0.0

    # ---- Gestion de la grille ----

    def ensure_grid(self, symbol: str, price: float):
        sym_state = self.state[symbol]
        center = sym_state.get("center")
        if center is None or abs(price - center) / center > REBALANCE_OUTSIDE_PCT:
            sym_state["center"] = price
            sym_state["grid"] = build_grid(price, GRID_WIDTH_PCT, GRID_LEVELS)
            logger.info(f"{symbol} : grille (re)centrée sur {price} -> {sym_state['grid']}")

    def step_pct(self) -> float:
        """Écart en % entre deux paliers consécutifs de la grille."""
        return (2 * GRID_WIDTH_PCT) / GRID_LEVELS

    # ---- Un passage pour un actif donné ----

    def process_symbol(self, symbol: str, quote_balance: float) -> float:
        try:
            price = self.get_current_price(symbol)
        except Exception as e:
            logger.error(f"{symbol} : erreur de récupération du prix : {e}")
            return quote_balance

        self.ensure_grid(symbol, price)
        sym_state = self.state[symbol]
        grid = sym_state["grid"]
        positions = sym_state["positions"]  # {"niveau_achat": {"qty":..., "buy_price":...}}

        mid_index = len(grid) // 2
        buy_levels = [lvl for lvl in grid[:mid_index]]

        # --- Côté achat : le prix touche un palier d'achat libre ---
        for level in sorted(buy_levels, reverse=True):
            key = str(level)
            if key in positions:
                continue  # position déjà ouverte à ce palier
            if abs(price - level) / level > TOUCH_TOLERANCE_PCT:
                continue

            amount = self.risk.max_order_size()
            allowed, reason = self.risk.check_order(amount, quote_balance)
            if not allowed:
                logger.warning(f"{symbol} : achat refusé au niveau {level} : {reason}")
                continue

            quantity = round(amount / level, 6)
            if self.risk.config.dry_run:
                logger.info(f"[SIMULATION] {symbol} achat {quantity} au niveau {level} (~{amount:.2f} USDT)")
            else:
                order = self.client.order_limit_buy(symbol=symbol, quantity=quantity, price=str(level))
                logger.info(f"{symbol} : ordre d'achat réel envoyé : {order}")

            positions[key] = {"qty": quantity, "buy_price": level}
            quote_balance -= amount
            self._save_state()

        # --- Côté vente : le prix a atteint le palier au-dessus du prix d'achat ---
        for buy_level_key in list(positions.keys()):
            pos = positions[buy_level_key]
            target_sell = pos["buy_price"] * (1 + self.step_pct())
            if price < target_sell:
                continue

            qty = pos["qty"]
            if self.risk.config.dry_run:
                pnl = (price - pos["buy_price"]) * qty
                logger.info(
                    f"[SIMULATION] {symbol} vente {qty} (achetée à {pos['buy_price']}) "
                    f"au prix {price:.2f} -> P&L estimé : {pnl:.2f} USDT"
                )
                self.risk.record_trade_result(pnl)
            else:
                order = self.client.order_limit_sell(symbol=symbol, quantity=qty, price=str(round(price, 2)))
                logger.info(f"{symbol} : ordre de vente réel envoyé : {order}")
                # P&L réel calculé une fois l'ordre exécuté (laissé au suivi manuel
                # ou à une étape ultérieure qui lit l'historique des ordres).

            del positions[buy_level_key]
            self._save_state()

        return quote_balance

    def run_once(self):
        logger.info("--- Passage du bot grid trading ---")
        quote_balance = self.get_quote_balance()
        logger.info(f"Solde USDT disponible : {quote_balance:.2f}")

        for symbol in SYMBOLS:
            quote_balance = self.process_symbol(symbol, quote_balance)


if __name__ == "__main__":
    bot = GridTradingBot()
    bot.run_once()
