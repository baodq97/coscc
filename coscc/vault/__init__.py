"""The vault: secrets an agent may use and never see.

`store` keeps them, `rules` decides one use, `filters` finds a value in bytes, `runner` runs a
command with secrets in it, and `sources` gathers what a unit has written. Only
`coscc/features/vault.py` and `coscc/run.py` import this package.
"""

from coscc.vault.filters import Hit, forms, mask, scan
from coscc.vault.rules import REFUSALS, Refusal, policy, sentence
from coscc.vault.runner import Result, Use, record, run
from coscc.vault.sources import unit_sources
from coscc.vault.store import (
    MODES,
    NAME,
    TABLES,
    VAULT_STAGES,
    BadSecret,
    Secret,
    Store,
    generate,
)

__all__ = [
    "MODES",
    "NAME",
    "REFUSALS",
    "TABLES",
    "VAULT_STAGES",
    "BadSecret",
    "Hit",
    "Refusal",
    "Result",
    "Secret",
    "Store",
    "Use",
    "forms",
    "generate",
    "mask",
    "policy",
    "record",
    "run",
    "scan",
    "sentence",
    "unit_sources",
]
