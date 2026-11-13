"""Shared setup for the examples: path, client, required-env helper, --create flag.

Every example VALIDATES by default (execution_options=validate_only — Meta checks the payload and
creates nothing). Pass --create to actually create it. Everything created is PAUSED.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from meta_client import MetaClient  # noqa: E402

CREATE = "--create" in sys.argv
VALIDATE = not CREATE


def need(var: str) -> str:
    v = os.environ.get(var)
    if not v:
        sys.exit(f"Set {var} first (see references/finding-your-ids.md).")
    return v


def client() -> MetaClient:
    return MetaClient(account_id=need("AD_ACCOUNT_ID"))


def report(label: str, result) -> None:
    mode = "CREATED (PAUSED)" if CREATE else "VALIDATED (nothing created)"
    print(f"{label}: {mode} -> {result}")
