# Finding your own IDs

Nothing in this repo ships with an ad account, page, pixel or Instagram ID. Every call takes
yours. Here is how to find them with the token you already have.

All snippets assume `FACEBOOK_TOKEN` is set (see "Bring your own token" in the README).

## 1. Ad account

```python
from scripts.meta_client import MetaClient

# account_id is required by the constructor but not used by this call — any value works here.
for a in MetaClient(account_id="0").list_ad_accounts():
    print(a["account_id"], a["name"], a["account_status"])   # account_status 1 = active
```

Pass the bare `account_id` (no `act_` prefix needed — the client adds it) to every later call:

```python
m = MetaClient(account_id="<AD_ACCOUNT_ID>")
m.whoami()   # name, status, currency, timezone, owning business
```

No accounts listed? The token's user or System User has not been given access to any ad account.
Fix it in Business Settings → Users → System Users (or People) → Assign Assets.

## 2. Page

```python
for p in m.list_pages():          # pages this ad account can promote
    print(p["id"], p["name"])
```

A page your Business owns can still be missing here — it has to be **assigned to this ad
account**. See "Page must be assigned to the ad account" in `api-gotchas.md`.

## 3. Pixel (dataset)

```python
for px in sorted(m.list_pixels(), key=lambda x: x.get("last_fired_time") or "", reverse=True):
    print(px["id"], px["name"], px.get("last_fired_time"))
```

The one that fired most recently is usually the one you want.

## 4. Instagram user ID (optional)

Only needed for Instagram placements on PAC creatives.

```python
m.get("<PAGE_ID>", fields="instagram_business_account")
# -> {"instagram_business_account": {"id": "<IG_USER_ID>"}, "id": "<PAGE_ID>"}
```

No `instagram_business_account` in the response means no Instagram account is linked to that
page. Omit `ig_user_id` and the creative runs Facebook-only.

## Keeping them out of git

Put your IDs in environment variables or a local `.env`, never in code you commit:

```bash
# .env  (ignored by .gitignore)
FACEBOOK_TOKEN=...
AD_ACCOUNT_ID=...
PAGE_ID=...
PIXEL_ID=...
```
