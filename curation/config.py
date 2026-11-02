"""Pesi e soglie dello scoring di curation.

Tutti i valori sono modificabili dalla sidebar della dashboard; questi sono i default.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# CTR atteso per posizione organica (curva media di settore, desktop+mobile).
# Usata per stimare il gap di CTR e il traffico recuperabile.
EXPECTED_CTR = {
    1: 0.28, 2: 0.155, 3: 0.11, 4: 0.08, 5: 0.06,
    6: 0.047, 7: 0.038, 8: 0.031, 9: 0.026, 10: 0.022,
}
EXPECTED_CTR_BEYOND_10 = 0.01


def expected_ctr(position: float) -> float:
    if position is None or position != position:  # NaN
        return 0.0
    p = max(1, int(round(position)))
    return EXPECTED_CTR.get(p, EXPECTED_CTR_BEYOND_10 if p <= 20 else 0.003)


SIGNALS = {
    "decay": "Decadimento traffico",
    "striking": "Striking distance (pos. 4-20)",
    "ctr_gap": "CTR sotto le attese",
    "staleness": "Contenuto datato",
    "thin": "Contenuto scarno",
    "cannibalization": "Cannibalizzazione",
}


@dataclass
class Weights:
    decay: float = 1.0
    striking: float = 1.0
    ctr_gap: float = 0.7
    staleness: float = 0.6
    thin: float = 0.5
    cannibalization: float = 0.8


@dataclass
class Thresholds:
    # Decadimento: un calo pari a decay_full (0.5 = -50%) vale segnale pieno.
    decay_full: float = 0.5
    min_clicks_prev: int = 20          # sotto questa soglia il trend non è significativo
    # Striking distance
    striking_min_pos: float = 4.0
    striking_max_pos: float = 20.0
    target_position: int = 3           # posizione obiettivo per stimare il potenziale
    # CTR
    min_impressions_ctr: int = 300
    # Freschezza (giorni dall'ultimo aggiornamento)
    stale_start_days: int = 180
    stale_full_days: int = 730
    # Thin content
    min_words: int = 900
    # Cannibalizzazione
    cannibal_min_impressions: int = 50
    cannibal_max_position: float = 30.0
    # Selezione finale
    candidate_min_priority: float = 35.0


@dataclass
class ScoringConfig:
    weights: Weights = field(default_factory=Weights)
    thresholds: Thresholds = field(default_factory=Thresholds)
    # Mix tra intensità dei segnali e potenziale di traffico recuperabile.
    potential_share: float = 0.4

    def to_dict(self) -> dict:
        return asdict(self)
