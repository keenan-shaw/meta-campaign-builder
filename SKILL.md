---
name: meta-campaign-builder
description: "Build and manage Meta (Facebook/Instagram) ad campaigns, ad sets, ads, AND creatives end-to-end via the Marketing API. Use any time the user asks to create, edit, pause, resume, or inspect Meta campaigns/ad sets on their own ad accounts \u2014 including geo-targeted launches, audience splits, budget changes, status flips, cropping creatives, uploading images/videos, or building PAC creatives and ads. Triggers include: \"build me a campaign\", \"create ad sets\", \"set up targeting\", \"pause campaign X\", \"spin up a test\", \"duplicate ad set\", \"what's running on [account]\", \"add ZIPs to ad set\", \"launch this creative\", \"upload these images/videos\", \"build a PAC creative\", \"crop this 9:16\", or any request involving the Meta Ads API."
---
# Meta Campaign Builder

## What this skill does
End-to-end programmatic management of Meta ad campaigns/ad sets/ads/creatives via the Marketing API (v25.0). Covers the full launch path: campaign → ad set → creative upload → PAC creative → ad, plus listing, updating, pausing, and inspection.

It runs on **your own token and your own ad account**. Nothing is hardcoded: `MetaClient` takes `account_id` as a required argument and resolves the token from `FACEBOOK_TOKEN` → `$META_ENV` → `./.env`. For the full launch path the token needs `ads_management` (plus `publish_video` if you upload video); a System User token is the durable choice. See `references/finding-your-ids.md`.

## Workflow

### 1. Establish context (always do first)
Before any write operation, confirm:
- **Which ad account** — if the user hasn't given one, list theirs with `GET /me/adaccounts?fields=id,name,account_status` (see `references/finding-your-ids.md`). Never guess an ID. Validate the token works against it with `GET /act_<id>?fields=name,account_status,business`.
- **Which campaign** — list existing with `GET /act_<id>/campaigns?fields=id,name,objective,status,effective_status`. Decide attach-vs-new.
- **Which page** — list promotable pages with `GET /act_<id>/promote_pages`. Most lead/traffic ad sets require a `page_id` on `promoted_object`. If the desired page isn't listed, the account owner has to assign it via [Business Settings → Pages → Assign Ad Account](https://business.facebook.com/settings/pages). A Business can own a page without it being assigned to every ad account.
- **Which pixel** — list with `GET /act_<id>/adspixels`. Sort by `last_fired_time` to find the active one. For LEAD objectives, default `custom_event_type=LEAD` even if the pixel hasn't fired that event yet (Meta accepts it for paused ad sets).

### 2. Choose a sensible default config
Unless the user specifies otherwise, use these defaults for the most common path (lead gen → website pixel):

| Field | Default |
|---|---|
| Campaign `objective` | `OUTCOME_LEADS` |
| Campaign `status` | `PAUSED` (always — never auto-launch) |
| Campaign `special_ad_categories` | `[]` (none) — unless credit/employment/housing/social-issues content |
| Ad set `optimization_goal` | `OFFSITE_CONVERSIONS` |
| Ad set `billing_event` | `IMPRESSIONS` |
| Ad set `bid_strategy` | `LOWEST_COST_WITHOUT_CAP` |
| Ad set `destination_type` | `WEBSITE` |
| Ad set `daily_budget` | `1000` cents ($10) for tests |
| Ad set `status` | `PAUSED` |
| Ad set `start_time` | `now + 1 day` (ISO 8601, UTC) |
| Targeting age | 25–65 |
| Targeting `location_types` | `["home", "recent"]` |
| Targeting `advantage_audience` | `0` (off) — keep explicit |

For **awareness/traffic** campaigns: `objective=OUTCOME_AWARENESS` or `OUTCOME_TRAFFIC`, `optimization_goal=REACH` or `LINK_CLICKS`, no `promoted_object` needed.

### 3. Always test before bulk creation
If creating more than ~3 ad sets, create one first as a canary and verify its `targeting` / `promoted_object` echo back correctly via `GET /<adset_id>?fields=...`. Only then run the batch.

### 4. Use `validate_only` dry-runs for risky payloads
When unsure if Meta will accept a payload (custom targeting, large zip lists, unusual combinations), pass `execution_options=["validate_only"]` to create endpoints. Meta will validate without creating, and gives **explicit error messages** that the normal endpoint sometimes hides (notably: invalid ZIP codes — see gotchas).

### 5. Report back clearly
After a batch run, summarize:
- N created / N attempted
- Total reach (zips/audience size if known)
- Total daily budget if unpaused
- Any failures and what was filtered/dropped
- Direct link to Ads Manager: `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=<ID>`

## Key patterns

### Creating a campaign
```python
from scripts.meta_client import MetaClient
m = MetaClient(account_id="<AD_ACCOUNT_ID>")
campaign_id = m.create_campaign(
    name="Brand - Test - 2026-05",
    objective="OUTCOME_LEADS",
    status="PAUSED",
)
```

### Creating an ad set (any targeting)
```python
m.create_adset(
    name="Test - National Adults",
    campaign_id=campaign_id,
    daily_budget_cents=1000,
    pixel_id="<PIXEL_ID>",
    page_id="<PAGE_ID>",
    targeting={
        "geo_locations": {"countries": ["US"]},
        "age_min": 25, "age_max": 65,
    },
)
```

### ZIP-targeted ad set (with built-in invalid-zip handling)
```python
m.create_adset_with_zips(
    name="G - TN-Alabama",
    campaign_id=campaign_id,
    pixel_id="<PIXEL_ID>",
    page_id="<PAGE_ID>",
    zips=["35004", "35005", ...],
    auto_filter_invalid=True,  # bisects to find bad ZIPs, removes, retries
)
```

### Listing / inspecting
```python
m.list_campaigns()  # all campaigns on account
m.list_adsets(campaign_id)
m.get_adset(adset_id)  # full detail
```

### Status flips
```python
m.update_status(adset_id, "PAUSED")  # or "ACTIVE"
m.update_status(campaign_id, "PAUSED")
```

## Creative pipeline (9:16 master → ads)
See `references/pac-creative.md` for the deep dive and hard rules.

### Step A — Crop one 9:16 master into placement variants
```bash
# images: produces out/4x5 + out/1x1, keeps the 9:16. No token, no ffmpeg needed.
python3 scripts/smart_crop_image.py --src ./input_images --out-dir ./out
# videos: needs opencv-python + ffmpeg on PATH
python3 scripts/smart_crop_video.py --src ./input_videos --out-dir ./out
```

### Step B — Get image hashes / video IDs
```python
m.upload_image("out/4x5/ad1_4x5.jpg")["hash"]   # upload new
m.list_images(name_filter="Brand")          # or pull existing from media library
m.list_videos()
# videos in bulk: python3 scripts/multi_aspect_upload.py --input-dir ./out
```

### Step C — Build the PAC creative + PAUSED ad (multi-text, non-dynamic)
```python
creative_id = m.create_pac_creative(
    name="Brand - Offer PAC v1", page_id="<PAGE_ID>",
    headlines=["H1","H2","H3"], bodies=["B1","B2"], description="Learn more today",
    feed_hash=feed_hash, story_hash=story_hash,
    link_url="https://example.com/offer", cta="LEARN_MORE",
)
ad_id = m.create_ad(name="Brand - Offer PAC v1", adset_id=adset_id, creative_id=creative_id)
```
**PAC, never Dynamic Creative.** Multi-text on a normal ad set must use `optimization_type=PLACEMENT` + `asset_customization_rules` — otherwise Meta throws error 1885998. The client validates the hard rules (≤5 titles, ≤5 bodies, exactly 1 description, two *different* image hashes, no `degrees_of_freedom_spec`). Full rationale in `references/pac-creative.md`.

## Implementation notes
- **Always paused on create.** Never set `status=ACTIVE` on first creation. Make activation an explicit second step.
- **Meta's 2,500-ZIP cap per ad set** is real. Anything larger must be split. The helper raises if over 2,500.
- **ZIPs silently drop vs. explicitly error inconsistently** — see `references/api-gotchas.md`. The `auto_filter_invalid=True` flag handles both behaviors.
- **`promoted_object` requires both `pixel_id` and `page_id`** for website-conversion lead ad sets, even though docs are unclear about the page requirement.
- **`start_time` is required** for ad sets, even paused ones. Default to +1 day.
- **Rate limiting:** insert a 0.4s sleep between calls when creating in bulk. Meta's per-user rate limit kicks in around 25 calls/sec but the API throttles unpredictably above ~5/sec for write ops.
- **Bring your own token.** Set `FACEBOOK_TOKEN` in the environment, or put `FACEBOOK_TOKEN=...` in a `.env` (path in `$META_ENV`, or `./.env`). Never commit it — `.gitignore` covers `.env*` and `*.env`.

## What this skill does NOT do
- **Generate creative from scratch** — it crops, uploads, and assembles existing assets; it doesn't design images or write the master copy.
- **Manage custom audiences** beyond listing them.
- **Modify pixels or conversions** — read-only on those.

Note: Instagram placements ARE supported — find your `ig_user_id` with the steps in `references/finding-your-ids.md` and pass it to `create_pac_creative()`; omit it for Facebook-only. The video path needs `opencv-python` and `ffmpeg` on PATH.

## References
- `scripts/meta_client.py` — Python helper class with all primitives (campaigns, ad sets, uploads, PAC creatives, ads)
- `scripts/smart_crop_image.py` / `smart_crop_video.py` — 9:16 master → 4:5 + 1:1 placement variants
- `scripts/multi_aspect_upload.py` — bulk-upload multi-aspect video sets → video_id manifest
- `scripts/build_pac_creative.py` — CLI to build + POST a PAC creative (and optional PAUSED ad)
- `scripts/requirements.txt` — Python deps (Pillow, numpy, requests, opencv-python)
- `references/pac-creative.md` — PAC pattern, hard rules, end-to-end flow
- `references/api-gotchas.md` — quirks learned the hard way (zip validation, page assignment, etc.)
- `references/finding-your-ids.md` — how to find your own ad account, page, pixel and Instagram IDs
- `references/objectives.md` — Meta objective + optimization goal cheat sheet
