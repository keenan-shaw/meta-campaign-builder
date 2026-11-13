"""03 — A placement-customized (PAC) image creative: 4:5 feed + 9:16 story, multi-text.

    export FACEBOOK_TOKEN=... AD_ACCOUNT_ID=... PAGE_ID=...
    export FEED_HASH=...  STORY_HASH=...     # from m.upload_image(...)["hash"] or m.list_images()
    python3 examples/03_pac_image.py            # validate only
    python3 examples/03_pac_image.py --create   # create the creative

Crop the inputs first: python3 scripts/smart_crop_image.py --src ./input_images --out-dir ./out
Optional: IG_USER_ID for Instagram placements, ADSET_ID to also create a PAUSED ad (--create only).
"""
import os

from _common import CREATE, VALIDATE, client, need, report

m = client()
result = m.create_pac_creative(
    name="Example · PAC image v1",
    page_id=need("PAGE_ID"),
    headlines=["See if you qualify", "Check your options in 60 seconds", "Free, no-obligation review"],
    bodies=["Short hook line.\n\nOne line of reframe.\n\nTap to start. 👉",
            "A second body variant to test against the first."],
    description="Learn more today",
    feed_hash=need("FEED_HASH"),
    story_hash=need("STORY_HASH"),
    link_url="https://example.com/offer",
    cta="LEARN_MORE",
    ig_user_id=os.environ.get("IG_USER_ID"),
    validate_only=VALIDATE,
)
report("PAC image creative", result)

if CREATE and os.environ.get("ADSET_ID"):
    report("ad", m.create_ad(name="Example · PAC image v1", adset_id=os.environ["ADSET_ID"],
                             creative_id=result))
