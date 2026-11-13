"""05 — One image, a pool of headlines/bodies as multiple text options, on a NORMAL ad.

Not Dynamic Creative: the ad set stays is_dynamic_creative=false and can hold many such ads.

    export FACEBOOK_TOKEN=... AD_ACCOUNT_ID=... PAGE_ID=... IMAGE_HASH=...
    python3 examples/05_multitext.py            # validate only
    python3 examples/05_multitext.py --create
"""
from _common import VALIDATE, client, need, report

m = client()
result = m.create_image_creative_multitext(
    name="Example · multi-text v1",
    page_id=need("PAGE_ID"),
    image_hash=need("IMAGE_HASH"),
    headlines=["See if you qualify", "Check your options", "Free review"],
    bodies=["Body variant A.\n\nTap to start. 👉", "Body variant B.", "Body variant C."],
    description="Learn more today",
    link_url="https://example.com/offer",
    validate_only=VALIDATE,
)
report("multi-text creative", result)
