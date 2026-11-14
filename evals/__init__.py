"""Evals that call Bedrock for real (spec §10). Run: .venv/bin/python -m evals"""
import os

# Evals read listings from data/mock_properties.py, not DynamoDB (set before agent loads).
os.environ.setdefault("LISTINGS_SOURCE", "seed")
