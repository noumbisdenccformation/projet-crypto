"""
risk_guard.py
=============
Garde-fous de risque communs aux deux bots (grid trading et DCA renforcé).

Ce module ne contient AUCUNE logique de trading — uniquement des règles de
sécurité que les deux stratégies doivent respecter avant de placer un ordre.
Objectif : rendre impossible, par construction, de reproduire le scénario
"10 %/semaine visés, 50 % du capital engagé".

Rien ici ne se connecte au réseau. Il est volontairement séparé pour que tu
puisses le relire et l'ajuster sans toucher à la logique de chaque stratégie.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os


@dataclass
class RiskConfig:
    # Part maximale du capital total risquée sur UN SEUL ordre/trade.
    max_risk_per_trade_pct: float = 0.02          # 2 %

    # Perte maximale cumulée tolérée sur une semaine glissante avant coupure
    # automatique du bot (arrêt complet, aucun nouvel ordre tant que non levé
    # manuellement).
    weekly_stop_loss_pct: float = 0.05            # -5 %

    # Part du capital qui doit TOUJOURS rester en stablecoin (USDT), jamais
    # investie, quelle que soit la stratégie. Sert de matelas de sécurité.
    min_reserve_pct: float = 0.30                 # 30 %

    # Tant que True, aucun ordre réel n'est envoyé à Binance : tout est
    # simulé et journalisé. À ne désactiver qu'après plusieurs semaines de
    # simulation jugées satisfaisantes.
    dry_run: bool = True

    # Fichier où l'état de risque (P&L de la semaine, coupure active ou non)
    # est persisté entre deux exécutions du bot.
    state_path: str = "risk_state.json"


class RiskGuard:
    """
    Contrôle, avant chaque ordre proposé par une stratégie, que les règles
    de risque sont respectées. Toute stratégie DOIT passer par
    `guard.check_order(...)` avant d'envoyer un ordre à l'exchange.
    """

    def __init__(self, config: RiskConfig, starting_capital_eur: float):
        self.config = config
        self.starting_capital_eur = starting_capital_eur
        self.state = self._load_state()

    # ---- Persistance simple sur disque (pas de base de données requise) ----

    def _load_state(self) -> dict:
        if os.path.exists(self.config.state_path):
            with open(self.config.state_path, "r") as f:
                return json.load(f)
        return {
            "week_start": datetime.utcnow().isoformat(),
            "week_pnl_eur": 0.0,
            "circuit_breaker_active": False,
        }

    def _save_state(self):
        with open(self.config.state_path, "w") as f:
            json.dump(self.state, f, indent=2)

    def _maybe_reset_week(self):
        week_start = datetime.fromisoformat(self.state["week_start"])
        if datetime.utcnow() - week_start >= timedelta(days=7):
            self.state["week_start"] = datetime.utcnow().isoformat()
            self.state["week_pnl_eur"] = 0.0
            self.state["circuit_breaker_active"] = False
            self._save_state()

    # ---- API utilisée par les stratégies ----

    def record_trade_result(self, pnl_eur: float):
        """À appeler après chaque trade clôturé (gagnant ou perdant)."""
        self._maybe_reset_week()
        self.state["week_pnl_eur"] += pnl_eur
        loss_limit = -abs(self.config.weekly_stop_loss_pct * self.starting_capital_eur)
        if self.state["week_pnl_eur"] <= loss_limit:
            self.state["circuit_breaker_active"] = True
        self._save_state()

    def max_order_size_eur(self) -> float:
        """Taille maximale autorisée pour un ordre, en euros."""
        return self.config.max_risk_per_trade_pct * self.starting_capital_eur

    def reserve_floor_eur(self) -> float:
        """Montant en stablecoin qui ne doit jamais être entamé."""
        return self.config.min_reserve_pct * self.starting_capital_eur

    def check_order(self, proposed_amount_eur: float, current_stable_balance_eur: float) -> tuple[bool, str]:
        """
        Retourne (autorisé: bool, raison: str).
        Toute stratégie doit vérifier `autorisé` avant d'envoyer l'ordre.
        """
        self._maybe_reset_week()

        if self.state["circuit_breaker_active"]:
            return False, "Coupure hebdomadaire active : perte limite atteinte, aucun ordre tant que non levée manuellement."

        if proposed_amount_eur > self.max_order_size_eur():
            return False, (
                f"Ordre refusé : {proposed_amount_eur:.2f} € dépasse le maximum "
                f"autorisé par trade ({self.max_order_size_eur():.2f} €)."
            )

        remaining_after = current_stable_balance_eur - proposed_amount_eur
        if remaining_after < self.reserve_floor_eur():
            return False, (
                f"Ordre refusé : entamerait la réserve de sécurité "
                f"({self.reserve_floor_eur():.2f} € minimum en stablecoin)."
            )

        return True, "OK"
