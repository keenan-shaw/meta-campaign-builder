"""04 — A PAC video creative from video_ids (never image hashes — that builds a static frame).

    export FACEBOOK_TOKEN=... AD_ACCOUNT_ID=... PAGE_ID=...
    export FEED_VIDEO_ID=... STORY_VIDEO_ID=...      # numeric video_ids
    export POSTER_HASH=...                           # image hash used as the thumbnail
    python3 examples/04_pac_video.py            # validate only (always do this first)
    python3 examples/04_pac_video.py --create

Get the video_ids:
    python3 scripts/smart_crop_video.py --src ./input_videos --out-dir ./out
    python3 scripts/multi_aspect_upload.py --input-dir ./out   # -> multi_aspect_manifest.json
"""
import os

from _common import VALIDATE, client, need, report

poster = need("POSTER_HASH")
m = client()
result = m.create_pac_creative_video(
    name="Example · PAC video v1",
    page_id=need("PAGE_ID"),
    headlines=["See if you qualify", "Check your options in 60 seconds"],
    bodies=["Short hook line.\n\nTap to start. 👉"],
    description="Learn more today",
    feed_video={"video_id": need("FEED_VIDEO_ID"), "thumbnail_hash": poster},
    story_video={"video_id": need("STORY_VIDEO_ID"), "thumbnail_hash": poster},
    link_url="https://example.com/offer",
    ig_user_id=os.environ.get("IG_USER_ID"),
    validate_only=VALIDATE,
)
report("PAC video creative", result)
