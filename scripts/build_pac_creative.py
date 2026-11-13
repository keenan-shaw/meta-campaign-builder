#!/usr/bin/env python3
"""build_pac_creative.py — build + POST a Meta Placement Asset Customization creative.

This builds the proven "5 headlines x 5 bodies" Placement Asset Customization
(PAC) creative and, optionally, creates a PAUSED ad from it. PAC lets Meta pool
your titles and bodies and serve the right image per placement (4:5 in Feed, 9:16
in Story/Reels) WITHOUT the ad set being dynamic-creative.

Why PAC and not Dynamic Creative
--------------------------------
PAC uses `optimization_type: "PLACEMENT"` plus `asset_customization_rules` to map
a feed image to feed placements and a story image to everything else, while still
pooling all titles/bodies. The ad set must NOT be dynamic-creative. Do NOT add
`degrees_of_freedom_spec` (triggers error 3858028) and do NOT set
`is_dynamic_creative`.

Hard rules baked in (validated below)
-------------------------------------
  - MAX 5 titles
  - MAX 5 bodies
  - MAX 1 description
  - EXACTLY 2 images: a 4:5 feed hash + a 9:16 story hash (must be different)

Config (environment)
--------------------
    ACCESS_TOKEN     Meta Graph API access token (REQUIRED)
    AD_ACCOUNT_ID    e.g. act_<your-id> (REQUIRED unless --dry-run with no POST)
    PAGE_ID          Facebook Page id (REQUIRED)
    IG_USER_ID       Instagram user id (REQUIRED)

Usage
-----
    export ACCESS_TOKEN=... AD_ACCOUNT_ID=act_XXXX PAGE_ID=... IG_USER_ID=...
    python3 build_pac_creative.py \\
        --headline "H1" --headline "H2" --headline "H3" \\
        --body "B1" --body "B2" \\
        --description "Learn more today" \\
        --image-feed-hash <4:5 hash> --image-story-hash <9:16 hash> \\
        --link-url https://example.com/lp \\
        --cta LEARN_MORE --name "MyState PAC v1" \\
        --adset-id 123456789      # optional — also creates a PAUSED ad
        --dry-run                  # print payload JSON, no network calls
"""
import os
import json
import argparse
import sys

import requests

try:
    from meta_client import MetaClient  # reuse the guarded video asset_feed_spec builder
except Exception:
    MetaClient = None

META_API_VERSION = "v25.0"  # single source of truth


def resolve_token():
    """FACEBOOK_TOKEN / ACCESS_TOKEN env var -> $META_ENV -> ./.env. See meta_client.resolve_token."""
    from meta_client import resolve_token as _resolve
    return _resolve()

# Adlabel names — every title shares "title", every body shares "body" so Meta pools them.
LABEL_TITLE = "title"
LABEL_BODY = "body"
LABEL_DESC = "desc"
LABEL_IMG_FEED = "img_feed"
LABEL_IMG_STORY = "img_story"
LABEL_URL = "url"


def build_asset_feed_spec(headlines, bodies, description, feed_hash, story_hash, link_url, cta):
    """Build the exact working PAC asset_feed_spec shape. No forbidden fields."""
    titles = [{"text": t, "adlabels": [{"name": LABEL_TITLE}]} for t in headlines]
    body_assets = [{"text": b, "adlabels": [{"name": LABEL_BODY}]} for b in bodies]
    descriptions = [{"text": description, "adlabels": [{"name": LABEL_DESC}]}]
    images = [
        {"hash": feed_hash, "adlabels": [{"name": LABEL_IMG_FEED}]},
        {"hash": story_hash, "adlabels": [{"name": LABEL_IMG_STORY}]},
    ]
    link_urls = [{"website_url": link_url, "adlabels": [{"name": LABEL_URL}]}]

    common_labels = {
        "body_label": {"name": LABEL_BODY},
        "title_label": {"name": LABEL_TITLE},
        "description_label": {"name": LABEL_DESC},
        "link_url_label": {"name": LABEL_URL},
    }

    asset_customization_rules = [
        # Rule 1 — Feed placements get the 4:5 image.
        {
            "customization_spec": {
                "age_min": 18,
                "age_max": 65,
                "publisher_platforms": ["facebook", "instagram"],
                "facebook_positions": ["feed"],
                "instagram_positions": ["stream", "explore", "explore_home", "profile_feed"],
            },
            "image_label": {"name": LABEL_IMG_FEED},
            **common_labels,
            "priority": 1,
        },
        # Rule 2 — catch-all (story / reels / everything else) gets the 9:16 image.
        {
            "customization_spec": {"age_min": 18, "age_max": 65},
            "image_label": {"name": LABEL_IMG_STORY},
            **common_labels,
            "priority": 2,
        },
    ]

    return {
        "ad_formats": ["AUTOMATIC_FORMAT"],
        "optimization_type": "PLACEMENT",
        "bodies": body_assets,
        "titles": titles,
        "descriptions": descriptions,
        "images": images,
        "link_urls": link_urls,
        "call_to_action_types": [cta],
        "asset_customization_rules": asset_customization_rules,
    }


def validate(headlines, bodies, descriptions, feed_hash, story_hash):
    errors = []
    if not 1 <= len(headlines) <= 5:
        errors.append(f"need 1-5 headlines (MAX 5), got {len(headlines)}")
    if not 1 <= len(bodies) <= 5:
        errors.append(f"need 1-5 bodies (MAX 5), got {len(bodies)}")
    if len(descriptions) != 1:
        errors.append(f"need exactly 1 description (MAX 1), got {len(descriptions)}")
    if not feed_hash or not story_hash:
        errors.append("both --image-feed-hash (4:5) and --image-story-hash (9:16) are required")
    elif feed_hash == story_hash:
        errors.append("feed and story image hashes must be DIFFERENT (4:5 feed + 9:16 story)")
    return errors


def post_creative(account_id, token, name, object_story_spec, asset_feed_spec, validate_only=False):
    data = {
        "name": name,
        "object_story_spec": json.dumps(object_story_spec),
        "asset_feed_spec": json.dumps(asset_feed_spec),
        "access_token": token,
    }
    if validate_only:
        data["execution_options"] = json.dumps(["validate_only"])
    r = requests.post(
        f"https://graph.facebook.com/{META_API_VERSION}/{account_id}/adcreatives",
        data=data,
        timeout=60,
    )
    return r.json()


def post_ad(account_id, token, name, adset_id, creative_id):
    r = requests.post(
        f"https://graph.facebook.com/{META_API_VERSION}/{account_id}/ads",
        data={
            "name": name,
            "adset_id": adset_id,
            "creative": json.dumps({"creative_id": creative_id}),
            "status": "PAUSED",  # always create ads PAUSED
            "access_token": token,
        },
        timeout=60,
    )
    return r.json()


def main():
    ap = argparse.ArgumentParser(
        description="Build + POST a Meta Placement Asset Customization (PAC) creative (5 headlines x 5 bodies).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--headline", action="append", default=[], metavar="TEXT", help="Headline/title (repeatable, MAX 5)")
    ap.add_argument("--body", action="append", default=[], metavar="TEXT", help="Body text (repeatable, MAX 5)")
    ap.add_argument("--description", required=True, help="Link description (exactly 1)")
    ap.add_argument("--image-feed-hash", help="4:5 feed image hash (image mode)")
    ap.add_argument("--image-story-hash", help="9:16 story image hash (image mode; must differ from feed)")
    # --- video mode (uses video_ids, never image hashes) ---
    ap.add_argument("--video", action="store_true", help="Build a VIDEO PAC")
    ap.add_argument("--video-feed-id", help="4:5 feed video_id")
    ap.add_argument("--video-story-id", help="9:16 story video_id")
    ap.add_argument("--video-feed-thumb", help="poster image hash for the 4:5 video")
    ap.add_argument("--video-story-thumb", help="poster image hash for the 9:16 video")
    ap.add_argument("--validate", action="store_true", help="Send execution_options=[validate_only]: Meta checks the payload without creating anything (no spend). Best first run for video.")
    ap.add_argument("--link-url", required=True, help="Destination URL")
    ap.add_argument("--cta", default="LEARN_MORE", help="Call-to-action type")
    ap.add_argument("--name", required=True, help="Creative name")
    ap.add_argument("--account", default=os.environ.get("AD_ACCOUNT_ID"), help="act_XXXX (default $AD_ACCOUNT_ID)")
    ap.add_argument("--page-id", default=os.environ.get("PAGE_ID"), help="Facebook Page id (default $PAGE_ID)")
    ap.add_argument("--ig-user-id", default=os.environ.get("IG_USER_ID"), help="Instagram user id (optional; default $IG_USER_ID)")
    ap.add_argument("--adset-id", default=None, help="If given, also create a PAUSED ad in this ad set")
    ap.add_argument("--dry-run", action="store_true", help="Print the payload JSON and exit (no network calls)")
    args = ap.parse_args()

    headlines = args.headline
    bodies = args.body
    descriptions = [args.description]
    page_id = args.page_id
    ig_user_id = args.ig_user_id  # optional — IG placements skipped if absent

    object_story_spec = {"page_id": page_id}
    if ig_user_id:
        object_story_spec["instagram_user_id"] = ig_user_id

    if args.video:
        if MetaClient is None:
            ap.error("video mode needs meta_client.py importable (same folder)")
        miss = [n for n, v in (("--video-feed-id", args.video_feed_id),
                               ("--video-story-id", args.video_story_id),
                               ("--video-feed-thumb", args.video_feed_thumb),
                               ("--video-story-thumb", args.video_story_thumb)) if not v]
        if miss:
            ap.error("video mode requires: " + ", ".join(miss))
        if not (1 <= len(headlines) <= 5 and 1 <= len(bodies) <= 5):
            ap.error("need 1-5 headlines and 1-5 bodies")
        # reuse the guarded video builder — hard-fails if a screenshot hash is passed as a video_id
        asset_feed_spec = MetaClient.build_pac_asset_feed_spec_video(
            headlines, bodies, args.description,
            {"video_id": args.video_feed_id, "thumbnail_hash": args.video_feed_thumb},
            {"video_id": args.video_story_id, "thumbnail_hash": args.video_story_thumb},
            args.link_url, args.cta,
        )
    else:
        errors = validate(headlines, bodies, descriptions, args.image_feed_hash, args.image_story_hash)
        if errors:
            print("Validation failed:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            sys.exit(2)
        asset_feed_spec = build_asset_feed_spec(
            headlines, bodies, args.description, args.image_feed_hash, args.image_story_hash, args.link_url, args.cta
        )

    if args.dry_run:
        payload = {
            "name": args.name,
            "object_story_spec": object_story_spec,
            "asset_feed_spec": asset_feed_spec,
        }
        if args.adset_id:
            payload["_ad"] = {"name": args.name, "adset_id": args.adset_id, "status": "PAUSED"}
        print(json.dumps(payload, indent=2))
        return

    # Live mode — token + account + page required (IG is optional).
    token = resolve_token()
    account_id = args.account
    missing = [n for n, v in (("token (FACEBOOK_TOKEN/ACCESS_TOKEN)", token),
                              ("--account / AD_ACCOUNT_ID", account_id),
                              ("--page-id / PAGE_ID", page_id)) if not v]
    if missing:
        ap.error("Missing required config: " + ", ".join(missing))

    mode = "VIDEO" if args.video else "image"
    verb = "Validating" if args.validate else "Creating"
    print(f"{verb} {mode} PAC creative '{args.name}' ({len(headlines)} titles x {len(bodies)} bodies)...")
    result = post_creative(account_id, token, args.name, object_story_spec, asset_feed_spec, validate_only=args.validate)
    if args.validate:
        if result.get("error"):
            print(f"VALIDATION FAILED:\n{json.dumps(result, indent=2)}", file=sys.stderr)
            sys.exit(1)
        print(f"VALIDATION PASSED — Meta accepts this {mode} payload (nothing created).")
        print(json.dumps(result, indent=2))
        return
    creative_id = result.get("id")
    if not creative_id:
        print(f"Creative creation FAILED: {json.dumps(result, indent=2)}", file=sys.stderr)
        sys.exit(1)
    print(f"creative_id: {creative_id}")

    if args.adset_id:
        print(f"Creating PAUSED ad in ad set {args.adset_id}...")
        ad_result = post_ad(account_id, token, args.name, args.adset_id, creative_id)
        ad_id = ad_result.get("id")
        if not ad_id:
            print(f"Ad creation FAILED: {json.dumps(ad_result, indent=2)}", file=sys.stderr)
            sys.exit(1)
        print(f"ad_id: {ad_id} (status PAUSED)")


if __name__ == "__main__":
    main()
