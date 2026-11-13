"""01 — A paused lead-gen campaign.

    export FACEBOOK_TOKEN=... AD_ACCOUNT_ID=...
    python3 examples/01_campaign.py            # validate only
    python3 examples/01_campaign.py --create   # create it, PAUSED
"""
from _common import VALIDATE, client, report

m = client()
result = m.create_campaign(
    name="Example · Lead Gen · Test",
    objective="OUTCOME_LEADS",
    special_ad_categories=[],      # set e.g. ["CREDIT"] or ["HOUSING"] if your offer is regulated
    validate_only=VALIDATE,
)
report("campaign", result)
