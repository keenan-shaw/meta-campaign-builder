# Examples

Five worked examples, smallest first. Every one **validates by default** — Meta checks the payload
with `execution_options=validate_only` and creates nothing. Add `--create` to build it for real;
everything is created **PAUSED**.

| File | Builds | Needs (env) |
|---|---|---|
| `01_campaign.py` | Lead-gen campaign | `AD_ACCOUNT_ID` |
| `02_zip_adset.py` | ZIP-targeted ad set, invalid ZIPs filtered | `CAMPAIGN_ID` `PIXEL_ID` `PAGE_ID` |
| `03_pac_image.py` | PAC image creative (4:5 feed + 9:16 story) | `PAGE_ID` `FEED_HASH` `STORY_HASH` |
| `04_pac_video.py` | PAC video creative | `PAGE_ID` `FEED_VIDEO_ID` `STORY_VIDEO_ID` `POSTER_HASH` |
| `05_multitext.py` | One image, multiple text options, non-DC | `PAGE_ID` `IMAGE_HASH` |

All of them also need `FACEBOOK_TOKEN` and `AD_ACCOUNT_ID`. Find your IDs with
`references/finding-your-ids.md`. Nothing here contains a real ID.

```bash
export FACEBOOK_TOKEN=... AD_ACCOUNT_ID=...
python3 examples/01_campaign.py
```
