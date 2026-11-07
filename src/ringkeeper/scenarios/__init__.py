"""The self-test: real requests through the real router, workers and gateway.

`catalog` says what each scenario must do, `world` builds two made-up clients, `driver` plays the
person answering, `checks` judges from what's recorded, and `report` writes it up. `host` runs all
of that in a process of its own with a scratch folder, and `launcher` starts it, so an install's
own data, vault and keychain entry are never part of a run.
"""
