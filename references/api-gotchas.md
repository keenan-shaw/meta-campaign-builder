# Meta Marketing API Gotchas

Hard-won lessons. Read before debugging.

## ZIP code validation is inconsistent
Meta's behavior changes mid-session:

- **First call with a fresh ZIP list:** Meta silently drops unknown ZIPs and creates the ad set with whatever it accepts. Example: requested 1,450 ZIPs, ad set returned 928. No error.
- **Subsequent calls:** Meta returns an explicit error listing the bad ZIPs: `"Invalid zip code: US:75064, US:75059"`. Easy to parse and filter.
- **After you've filtered explicit ones:** Meta switches to a generic error with `error_subcode=1487249` and message "Invalid ZIP Codes in targeting" — **no list of which ZIPs are bad.**

**Solution:** use `execution_options=["validate_only"]` as a dry-run. The validate endpoint surfaces explicit bad ZIPs that the real endpoint hides. Combined with bisection (split list, test halves, recurse), you can isolate every bad ZIP in O(log N) calls.

The helper `MetaClient.create_adset_with_zips(auto_filter_invalid=True)` does this automatically.

**Causes of bad ZIPs:** PO boxes, military APO/FPO, decommissioned/merged ZIPs, ZCTA-only codes that USPS still recognizes but Meta doesn't, non-deliverable industrial zones.

## 2,500-ZIP cap per ad set
Hard limit on `targeting.geo_locations.zips`. The API will accept up to 2,500 entries; over that, it rejects the whole payload. Use `MetaClient.split_zips()` to chunk evenly.

## Page must be assigned to the ad account, not just owned by the business
A page can be owned by your Business Manager but still not show up in `/act_X/promote_pages` if it's not explicitly assigned to that specific ad account. Symptom: an unrelated page is the only "promotable page" on an ad account, even though the same Business owns a dozen pages including the one you want.

**Fix:** [Business Settings → Pages → \<page\> → Assigned Assets → Add Ad Account](https://business.facebook.com/settings/pages) with at least "Create Ads" task permission. Takes ~30 seconds. New assignment shows up in the API within seconds.

## `promoted_object` requires `page_id` for many objectives
The docs are vague but `OUTCOME_LEADS` website ad sets need both `pixel_id` AND `page_id` in `promoted_object`, even when paused with no ad attached. Without `page_id` you get vague errors. The helper sets both.

## `start_time` required even for PAUSED ad sets
Meta rejects ad set creation without `start_time`. Default to `now + 1 day` (UTC, ISO 8601 with `+0000` suffix).

## Custom event types for pixels that haven't fired the event
You can set `custom_event_type=LEAD` on an ad set even if the pixel has never fired a `Lead` event. Meta won't complain at creation time. It only matters when the ad set is unpaused and tries to optimize.

## Rate limiting
Write operations throttle unpredictably above ~5/sec. Bulk creation should sleep ~0.3–0.4s between calls. Burst is fine for the first few; sustained throughput needs the delay.

## Status flips don't cascade
Pausing a campaign does NOT pause its ad sets — `effective_status` reflects the paused-by-parent state but the ad set's own `status` field stays whatever it was. When activating later, you need to flip both campaign AND each ad set to `ACTIVE`.

## Token scopes matter
What the token can do is decided by its scopes, not by the code:
- `ads_management` — create campaigns, ad sets, creatives and ads; read everything
- `publish_video` — needed for video uploads
- `business_management` — only if you manage Business-level assets (page assignment etc.)

A token missing `ads_management` fails at the creative/ad step with a permission error even though reads work. Check what yours has with `GET /debug_token?input_token=<token>`.

## `validate_only` is your friend
Always available, never creates anything, returns real errors. Use it to:
- Sanity-check a targeting payload before bulk creation
- Find invalid ZIPs/cities/regions
- Estimate audience size without committing (combine with `delivery_estimate` endpoint)
- Test new ad set configs against a campaign you don't want to clutter
