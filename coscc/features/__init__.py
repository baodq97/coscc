"""The features the app carries: add a file here and its `PLUGIN` on one line, delete the line to remove it."""

from coscc.features import codegraph, decisions, notices, parallel, scan, scratch, vault

FEATURES = (
    codegraph.PLUGIN,
    decisions.PLUGIN,
    notices.PLUGIN,
    parallel.PLUGIN,
    scan.PLUGIN,
    scratch.PLUGIN,
    vault.PLUGIN,
)
