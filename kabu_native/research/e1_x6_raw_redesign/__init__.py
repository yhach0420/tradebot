"""E1_X6 Plan V3 raw-feature redesign (research-only package).

NOT importable from the Paper runtime path (lives under kabu_native/research/,
which is never on Paper's PYTHONPATH: Paper uses kabu_native/src). Never imports
broker / order submit / cancel / Discord modules. No external communication.
"""
