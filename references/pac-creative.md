# PAC Creative — Placement Asset Customization (the working multi-text pattern)

Merged in from the meta-ads-launch-kit (2026-06-08). This is the **verified v25.0
pattern** for shipping multi-headline / multi-body ads on a **normal (non-dynamic)
ad set**. Use it instead of Dynamic Creative.

## Why PAC and not Dynamic Creative

Meta v25.0 auto-classifies an `asset_feed_spec` with multiple bodies/titles as
**Dynamic Creative**. If the ad set is NOT `is_dynamic_creative: true`, the API
throws **error 1885998** ("Cannot Create Dynamic Creative ad In Non-Dynamic
Creative Ad Set").

PAC sidesteps this with `optimization_type: "PLACEMENT"` + `asset_customization_rules`:
Meta pools all titles/bodies AND serves the right image per placement (4:5 in Feed,
9:16 in Story/Reels) — no DC flag needed. PAC ads don't burn the DC ad-account quota,
don't dilute the learning signal across placements, and survive copies between ad sets.

**Never** set `is_dynamic_creative: true` unless the user explicitly types those words.

## Hard rules (validated by `MetaClient.create_pac_creative` and `build_pac_creative.py`)

- **MAX 5 titles**, **MAX 5 bodies**, **EXACTLY 1 description**.
- **EXACTLY 2 images**: a 4:5 feed hash + a 9:16 story hash — and they must be **different**.
- **NEVER add `degrees_of_freedom_spec`** → triggers error 3858028.
- **NEVER set `is_dynamic_creative`** on the ad set.
- Description renders as gray fine print on Feed — keep it generic ("Learn more today"),
  never put a real selling line there.
- `object_story_spec` is minimal: `page_id` (+ `instagram_user_id` if you have one).

## End-to-end flow (programmatic)

```python
from scripts.meta_client import MetaClient
m = MetaClient(account_id="<AD_ACCOUNT_ID>")

# 1. Upload the cropped assets (or pull existing hashes with m.list_images())
feed  = m.upload_image("out/4x5/ad1_4x5.jpg")["hash"]   # 4:5 feed
story = m.upload_image("out/ad1.jpg")["hash"]           # 9:16 story (original)

# 2. Build + POST the PAC creative
creative_id = m.create_pac_creative(
    name="Brand - Offer PAC v1",
    page_id="<PAGE_ID>",
    headlines=["Headline A", "Headline B", "Headline C"],
    bodies=["Body 1", "Body 2"],
    description="Learn more today",
    feed_hash=feed, story_hash=story,
    link_url="https://example.com/offer",
    cta="LEARN_MORE",
    # ig_user_id="..."   # optional — omit to skip IG placements
)

# 3. Attach to an existing PAUSED ad set as a PAUSED ad
ad_id = m.create_ad(name="Brand - Offer PAC v1",
                    adset_id="<adset_id>", creative_id=creative_id)
```

## End-to-end flow (CLI)

```bash
# images: 9:16 master -> 4:5 + 1:1 (keeps the 9:16)
python3 scripts/smart_crop_image.py --src ./input_images --out-dir ./out

# build creative + PAUSED ad straight from hashes
python3 scripts/build_pac_creative.py \
  --name "Brand - Offer PAC v1" \
  --account act_<AD_ACCOUNT_ID> --page-id <PAGE_ID> \
  --headline "H1" --headline "H2" --headline "H3" \
  --body "B1" --body "B2" \
  --description "Learn more today" \
  --image-feed-hash <4:5 hash> --image-story-hash <9:16 hash> \
  --link-url https://example.com/offer --cta LEARN_MORE \
  --adset-id <adset_id>            # optional — also creates the PAUSED ad
  # --dry-run                       # print payload, no network
```

## Video variant

Video PAC uses `video_id`s instead of image `hash`es (same rule structure). Pipeline:

```bash
python3 scripts/smart_crop_video.py    --src ./input_videos --out-dir ./out  # 9:16 -> 4:5 + 1:1
python3 scripts/multi_aspect_upload.py --input-dir ./out                     # -> multi_aspect_manifest.json (video_ids)
```

**Then build the VIDEO creative from those `video_id`s** (added 2026-07-09 — this step was previously prose-only, which silently degraded video → a static frame):

```python
from meta_client import MetaClient
m = MetaClient("<account_id>")
# Single video ad — the correct 'video as video' primitive:
cid = m.create_video_creative(name, page_id, video_id, message, link_url,
                              thumbnail_hash=<poster_hash>)      # poster REQUIRED
# OR multi-aspect video PAC (feed 4:5 + story 9:16) — DRY-RUN first:
m.create_pac_creative_video(name, page_id, headlines, bodies, description,
                            feed_video={"video_id": <4x5_id>, "thumbnail_hash": <h>},
                            story_video={"video_id": <9x16_id>, "thumbnail_hash": <h>},
                            link_url, validate_only=True)        # always validate before real create
```

- **Never** feed a video into `create_pac_creative` (image-only: `feed_hash`/`story_hash`) — that builds a **static image ad from a frame.** Use the `*_video` functions; they **hard-fail** if a `video_id` isn't numeric (guard against a screenshot hash slipping through).
- Every video needs a **poster** — `thumbnail_hash` (via `upload_image`) or `thumbnail_url` (the video's `picture` from `list_videos`). The poster is the thumbnail; the `video_id` is the asset.
- Route by `MetaClient.media_type_of(path)` → `'video' | 'image'`.

Requires **opencv-python** (`pip install opencv-python`) and **ffmpeg** on PATH
(`brew install ffmpeg`). The image path needs neither.

## Tokens / config

All scripts resolve the token in this order: `$FACEBOOK_TOKEN` → `$ACCESS_TOKEN` →
the file named in `$META_ENV` → `./.env` in the working directory. Nothing else is read.
To upload images/videos and create creatives + ads, the token needs **`ads_management`**
(plus **`publish_video`** for video). A System User token is the durable option.

Find your Instagram user ID with the steps in `finding-your-ids.md`. Pass `ig_user_id` to
`create_pac_creative()` / `--ig-user-id` to enable IG placements; omit it to run Facebook-only.
```
