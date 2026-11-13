"""02 — A ZIP-targeted ad set on an existing campaign.

    export FACEBOOK_TOKEN=... AD_ACCOUNT_ID=... CAMPAIGN_ID=... PIXEL_ID=... PAGE_ID=...
    python3 examples/02_zip_adset.py            # validate only
    python3 examples/02_zip_adset.py --create   # bisect out invalid ZIPs, then create PAUSED

CAMPAIGN_ID must be a real (paused) campaign — run 01_campaign.py --create first.
If that campaign has a Special Ad Category (CREDIT, HOUSING, ...), Meta rejects custom ages and
ZIP targeting is restricted: use a campaign with special_ad_categories=[] for this example.
"""
from _common import CREATE, client, need, report

ZIPS = ["90210", "92101", "94103", "95814", "00000"]   # 00000 is invalid on purpose

m = client()
common = dict(campaign_id=need("CAMPAIGN_ID"), pixel_id=need("PIXEL_ID"), page_id=need("PAGE_ID"),
              daily_budget_cents=1000)

if CREATE:
    # create_adset_with_zips runs its own validate_only bisection, drops bad ZIPs, then creates.
    adset_id, info = m.create_adset_with_zips(name="Example · CA ZIPs", zips=ZIPS, **common)
    report("zip ad set", {"id": adset_id, **info})
else:
    targeting = {
        "geo_locations": {"zips": [{"key": f"US:{z}"} for z in ZIPS if z != "00000"],
                          "location_types": ["home", "recent"]},
        "age_min": 25, "age_max": 65,
        "targeting_automation": {"advantage_audience": 0},
    }
    report("zip ad set", m.create_adset(name="Example · CA ZIPs", targeting=targeting,
                                        validate_only=True, **common))
