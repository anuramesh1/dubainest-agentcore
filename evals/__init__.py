"""Evals that call Bedrock for real (spec §10). Run: .venv/bin/python -m evals"""

import os

# Evals read listings from data/mock_properties.py, not DynamoDB (set before agent loads),
# and keep memory in-process: they never touch the AWS memory tables.
os.environ.setdefault("LISTINGS_SOURCE", "seed")
os.environ.pop("SESSIONS_TABLE", None)
os.environ.pop("MEMORY_TABLE", None)
