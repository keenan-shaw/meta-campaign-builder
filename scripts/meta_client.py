"""Meta Marketing API helper for the meta-campaign-builder skill.

Wraps the Meta Graph API with sensible defaults and the patterns learned
running lead-gen ad accounts. Read SKILL.md before using.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

API_VERSION = "v25.0"
BASE = f"https://graph.facebook.com/{API_VERSION}"


class MetaAPIError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:300]}")


class MetaClient:
    def __init__(self, account_id: str, token: str | None = None):
        self.account_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        self.token = token or resolve_token()
        if not self.token:
            raise RuntimeError(TOKEN_HELP)

    # ---------- low-level ----------
    _RETRY_MAX = 4  # transient-error retries (exponential backoff: 1, 2, 4s)

    @staticmethod
    def _is_transient(status: int, body: str, is_mutation: bool) -> bool:
        b = body or ""
        signaled = ('"is_transient":true' in b or '"code":1,' in b
                    or '"code":2,' in b or "reduce the amount of data" in b)
        # Mutations retry ONLY when Meta signals the request didn't process (avoids duplicate creates).
        # Reads also retry any 5xx.
        return signaled if is_mutation else (status >= 500 or signaled)

    def _request(self, method: str, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        params["access_token"] = self.token
        url = f"{BASE}/{path}"
        is_mutation = method != "GET"
        if is_mutation:
            data = urllib.parse.urlencode(params).encode()
        else:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        last: Exception | None = None
        for attempt in range(self._RETRY_MAX):
            try:
                req = (urllib.request.Request(url, data=data, method=method) if is_mutation
                       else urllib.request.Request(url, method="GET"))
                resp = urllib.request.urlopen(req).read().decode()
                return json.loads(resp) if resp else {}
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                last = MetaAPIError(e.code, body)
                if attempt < self._RETRY_MAX - 1 and self._is_transient(e.code, body, is_mutation):
                    time.sleep(2 ** attempt)
                    continue
                raise last from None
            except urllib.error.URLError as e:  # network hiccup — retry reads only (POST could have landed)
                last = e
                if attempt < self._RETRY_MAX - 1 and not is_mutation:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last  # for type-checkers; loop always returns or raises

    def get(self, path: str, fields: str | None = None, **params) -> dict:
        if fields:
            params["fields"] = fields
        return self._request("GET", path, params)

    def post(self, path: str, **params) -> dict:
        return self._request("POST", path, params)

    # ---------- discovery ----------
    def list_ad_accounts(self) -> list[dict]:
        """Ad accounts this token can reach (independent of self.account_id)."""
        return self.get("me/adaccounts", fields="id,account_id,name,account_status,business").get("data", [])

    def whoami(self) -> dict:
        return self.get(self.account_id, fields="name,account_status,currency,timezone_name,business")

    def list_campaigns(self, limit: int = 50) -> list[dict]:
        return self.get(
            f"{self.account_id}/campaigns",
            fields="id,name,objective,status,effective_status,buying_type,daily_budget,lifetime_budget",
            limit=limit,
        ).get("data", [])

    def list_adsets(self, campaign_id: str | None = None, limit: int = 50) -> list[dict]:
        path = f"{campaign_id}/adsets" if campaign_id else f"{self.account_id}/adsets"
        return self.get(
            path,
            fields="id,name,status,effective_status,daily_budget,optimization_goal,billing_event,campaign_id",
            limit=limit,
        ).get("data", [])

    def get_adset(self, adset_id: str) -> dict:
        return self.get(
            adset_id,
            fields="id,name,status,daily_budget,optimization_goal,billing_event,"
                   "destination_type,promoted_object,targeting,start_time,end_time,campaign_id",
        )

    def list_pages(self) -> list[dict]:
        """Pages promotable from this ad account."""
        return self.get(f"{self.account_id}/promote_pages", fields="id,name").get("data", [])

    def list_pixels(self) -> list[dict]:
        return self.get(
            f"{self.account_id}/adspixels",
            fields="id,name,last_fired_time,is_unavailable",
        ).get("data", [])

    def list_custom_audiences(self, limit: int = 50) -> list[dict]:
        return self.get(
            f"{self.account_id}/customaudiences",
            fields="id,name,subtype,approximate_count_lower_bound,delivery_status",
            limit=limit,
        ).get("data", [])

    # ---------- creation ----------
    # Multi-text bundle. VERIFIED 2026-07-09: visible/deliverable multiple primary texts REQUIRE
    # advantage_plus_creative OPT_IN — it's an Advantage+ creative feature. With OPT_OUT, Meta strips
    # object_story_spec.message and Ads Manager won't surface the asset_feed_spec pool (looks blank).
    # So the master MUST be OPT_IN for multi-text. Trade-off for
    # compliance-burned creatives: Meta may apply visual enhancements; the API strips per-feature
    # OPT_OUT overrides, so disable the visual enhancements PER AD in the Ads Manager UI (that control
    # still works) — or accept low risk on video. Default here = OPT_IN.
    _TEXT_OPT_DOF = {"creative_features_spec": {
        "advantage_plus_creative": {"enroll_status": "OPT_IN"},
        "text_optimizations": {"enroll_status": "OPT_IN"},
    }}

    def create_campaign(
        self,
        name: str,
        objective: str = "OUTCOME_LEADS",
        status: str = "PAUSED",
        special_ad_categories: list[str] | None = None,
        buying_type: str = "AUCTION",
        validate_only: bool = False,
        **extra,
    ):
        params = {
            "name": name,
            "objective": objective,
            "status": status,
            "special_ad_categories": json.dumps(special_ad_categories or []),
            "buying_type": buying_type,
            # Meta now requires this field explicitly (subcode 4834011). Default False = ABO
            # (budget lives on the ad set). Caller can override via extra for CBO budget sharing.
            "is_adset_budget_sharing_enabled": "false",
            **extra,
        }
        if validate_only:
            params["execution_options"] = json.dumps(["validate_only"])
        resp = self.post(f"{self.account_id}/campaigns", **params)
        return resp.get("id", resp)  # validation response when validate_only=True

    def create_adset(
        self,
        name: str,
        campaign_id: str,
        targeting: dict,
        pixel_id: str | None = None,
        page_id: str | None = None,
        custom_event_type: str = "LEAD",
        daily_budget_cents: int = 1000,
        optimization_goal: str = "OFFSITE_CONVERSIONS",
        billing_event: str = "IMPRESSIONS",
        bid_strategy: str = "LOWEST_COST_WITHOUT_CAP",
        destination_type: str = "WEBSITE",
        status: str = "PAUSED",
        start_time: str | None = None,
        validate_only: bool = False,
        **extra,
    ) -> dict:
        """Create an ad set. Returns the API response dict (has 'id' on success)."""
        if start_time is None:
            start_time = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).strftime(
                "%Y-%m-%dT%H:%M:%S+0000"
            )
        promoted_object = {}
        if pixel_id:
            promoted_object["pixel_id"] = pixel_id
            promoted_object["custom_event_type"] = custom_event_type
        if page_id:
            promoted_object["page_id"] = page_id

        params = {
            "name": name,
            "campaign_id": campaign_id,
            "status": status,
            "daily_budget": daily_budget_cents,
            "billing_event": billing_event,
            "optimization_goal": optimization_goal,
            "bid_strategy": bid_strategy,
            "destination_type": destination_type,
            "targeting": json.dumps(targeting),
            "start_time": start_time,
            **extra,
        }
        if promoted_object:
            params["promoted_object"] = json.dumps(promoted_object)
        if validate_only:
            params["execution_options"] = json.dumps(["validate_only"])

        return self.post(f"{self.account_id}/adsets", **params)

    def create_adset_with_zips(
        self,
        name: str,
        campaign_id: str,
        zips: Iterable[str],
        pixel_id: str | None = None,
        page_id: str | None = None,
        age_min: int = 25,
        age_max: int = 65,
        auto_filter_invalid: bool = True,
        max_filter_iterations: int = 30,
        **adset_kwargs,
    ) -> tuple[str, dict]:
        """ZIP-targeted ad set creation with invalid-zip handling.

        Returns (adset_id, info_dict) where info_dict has counts:
          {'requested': N, 'created_with': N, 'filtered_invalid': [...]}.

        If auto_filter_invalid=True and Meta rejects, uses validate_only
        bisection to find every bad ZIP, then creates the ad set with the
        clean set.
        """
        zip_list = [str(z).zfill(5) for z in zips]
        original = len(zip_list)
        if original > 2500:
            raise ValueError(
                f"{original} zips exceeds Meta's 2500-per-ad-set cap. Split into multiple ad sets."
            )

        invalid: set[str] = set()

        def _targeting(z):
            return {
                "geo_locations": {
                    "zips": [{"key": f"US:{x}"} for x in z],
                    "location_types": ["home", "recent"],
                },
                "age_min": age_min,
                "age_max": age_max,
                "targeting_automation": {"advantage_audience": 0},
            }

        if auto_filter_invalid:
            # First pass: validate_only dry run, bisect on generic errors
            invalid |= self._find_invalid_zips_via_validation(
                campaign_id, zip_list, pixel_id, page_id,
                _targeting, max_filter_iterations, **adset_kwargs,
            )
            zip_list = [z for z in zip_list if z not in invalid]

        # Real create
        resp = self.create_adset(
            name=name, campaign_id=campaign_id,
            pixel_id=pixel_id, page_id=page_id,
            targeting=_targeting(zip_list), **adset_kwargs,
        )
        return resp.get("id"), {
            "requested": original,
            "created_with": len(zip_list),
            "filtered_invalid": sorted(invalid),
        }

    def _find_invalid_zips_via_validation(
        self, campaign_id, zip_list, pixel_id, page_id,
        targeting_fn, max_iter, **adset_kwargs,
    ) -> set[str]:
        """Bisect using validate_only dry-runs to find all invalid ZIPs."""
        bad: set[str] = set()
        queue: list[list[str]] = [list(zip_list)]
        iters = 0
        while queue and iters < max_iter:
            iters += 1
            batch = queue.pop()
            try:
                self.create_adset(
                    name=f"_validate_{iters}",
                    campaign_id=campaign_id,
                    pixel_id=pixel_id, page_id=page_id,
                    targeting=targeting_fn([z for z in batch if z not in bad]),
                    validate_only=True, **adset_kwargs,
                )
                continue  # batch is clean
            except MetaAPIError as e:
                explicit = set(re.findall(r"US:(\d{4,5})", e.body))
                if explicit:
                    bad |= explicit
                    continue
                # Generic "Invalid ZIP Codes" — bisect
                if "Invalid ZIP" in e.body or "1487249" in e.body:
                    clean_batch = [z for z in batch if z not in bad]
                    if len(clean_batch) <= 1:
                        bad |= set(clean_batch)
                        continue
                    mid = len(clean_batch) // 2
                    queue.append(clean_batch[:mid])
                    queue.append(clean_batch[mid:])
                else:
                    raise  # different error — surface it
            time.sleep(0.3)
        return bad

    # ---------- creative: media library ----------
    def list_images(self, limit: int = 25, name_filter: str | None = None) -> list[dict]:
        """Pull image assets from the account media library (returns hashes).

        Companion to the old meta-ads-creative-pull skill. Filter post-fetch by
        name substring if name_filter is given.
        """
        params = {"fields": "hash,name,width,height,url,created_time", "limit": limit}
        if name_filter:
            params["name_filter"] = name_filter  # Meta supports this on adimages
        data = self.get(f"{self.account_id}/adimages", **params).get("data", [])
        return data

    def list_videos(self, limit: int = 25) -> list[dict]:
        """Pull video assets from the account media library (returns video_ids)."""
        return self.get(
            f"{self.account_id}/advideos",
            fields="id,title,created_time,length,picture",
            limit=limit,
        ).get("data", [])

    # ---------- creative: uploads (need ads_management + publish_video) ----------
    def upload_image(self, path: str) -> dict:
        """Upload one image to /adimages. Returns {filename, hash}.

        Uses requests for multipart. Token must carry ads_management.
        """
        import requests  # lazy — only the upload path needs it
        fname = os.path.basename(path)
        with open(path, "rb") as fh:
            r = requests.post(
                f"{BASE}/{self.account_id}/adimages",
                files={"filename": (fname, fh, "image/jpeg")},
                data={"access_token": self.token},
                timeout=120,
            )
        j = r.json()
        if "images" not in j:
            raise MetaAPIError(r.status_code, json.dumps(j))
        # response shape: {"images": {"<fname>": {"hash": "...", "url": "..."}}}
        first = next(iter(j["images"].values()))
        return {"filename": fname, "hash": first["hash"], "url": first.get("url")}

    def upload_video(self, path: str, name: str | None = None) -> str:
        """Upload one video to /advideos. Returns video_id. Needs publish_video."""
        import requests  # lazy
        fname = os.path.basename(path)
        with open(path, "rb") as fh:
            r = requests.post(
                f"{BASE}/{self.account_id}/advideos",
                files={"source": (fname, fh, "video/mp4")},
                data={"name": name or fname.rsplit(".", 1)[0], "access_token": self.token},
                timeout=300,
            )
        j = r.json()
        if "id" not in j:
            raise MetaAPIError(r.status_code, json.dumps(j))
        return j["id"]

    # ---------- creative: PAC (Placement Asset Customization) ----------
    @staticmethod
    def build_pac_asset_feed_spec(
        headlines: list[str],
        bodies: list[str],
        description: str,
        feed_hash: str,
        story_hash: str,
        link_url: str,
        cta: str = "LEARN_MORE",
    ) -> dict:
        """Build the verified 5x5 PAC asset_feed_spec (no forbidden fields).

        Hard rules (validated by create_pac_creative): MAX 5 titles, MAX 5 bodies,
        EXACTLY 1 description, feed_hash != story_hash. Do NOT add
        degrees_of_freedom_spec or is_dynamic_creative — that breaks PAC.
        """
        L = {"title": "title", "body": "body", "desc": "desc",
             "img_feed": "img_feed", "img_story": "img_story", "url": "url"}
        common = {
            "body_label": {"name": L["body"]},
            "title_label": {"name": L["title"]},
            "description_label": {"name": L["desc"]},
            "link_url_label": {"name": L["url"]},
        }
        return {
            "ad_formats": ["AUTOMATIC_FORMAT"],
            "optimization_type": "PLACEMENT",
            "titles": [{"text": t, "adlabels": [{"name": L["title"]}]} for t in headlines],
            "bodies": [{"text": b, "adlabels": [{"name": L["body"]}]} for b in bodies],
            "descriptions": [{"text": description, "adlabels": [{"name": L["desc"]}]}],
            "images": [
                {"hash": feed_hash, "adlabels": [{"name": L["img_feed"]}]},
                {"hash": story_hash, "adlabels": [{"name": L["img_story"]}]},
            ],
            "link_urls": [{"website_url": link_url, "adlabels": [{"name": L["url"]}]}],
            "call_to_action_types": [cta],
            "asset_customization_rules": [
                {  # Rule 1 — feed placements get the 4:5 image
                    "customization_spec": {
                        "age_min": 18, "age_max": 65,
                        "publisher_platforms": ["facebook", "instagram"],
                        "facebook_positions": ["feed"],
                        "instagram_positions": ["stream", "explore", "explore_home", "profile_feed"],
                    },
                    "image_label": {"name": L["img_feed"]}, **common, "priority": 1,
                },
                {  # Rule 2 — catch-all (story/reels/everything else) gets the 9:16 image
                    "customization_spec": {"age_min": 18, "age_max": 65},
                    "image_label": {"name": L["img_story"]}, **common, "priority": 2,
                },
            ],
        }

    def create_pac_creative(
        self,
        name: str,
        page_id: str,
        headlines: list[str],
        bodies: list[str],
        description: str,
        feed_hash: str,
        story_hash: str,
        link_url: str,
        cta: str = "LEARN_MORE",
        ig_user_id: str | None = None,
        validate_only: bool = False,
    ):
        """Create a PAC adcreative and return creative_id (or the validation response when
        validate_only=True). Validates the hard rules locally first."""
        errs = []
        if not 1 <= len(headlines) <= 5:
            errs.append(f"need 1-5 headlines, got {len(headlines)}")
        if not 1 <= len(bodies) <= 5:
            errs.append(f"need 1-5 bodies, got {len(bodies)}")
        if not feed_hash or not story_hash:
            errs.append("both feed_hash (4:5) and story_hash (9:16) required")
        elif feed_hash == story_hash:
            errs.append("feed_hash and story_hash must be DIFFERENT")
        if errs:
            raise ValueError("PAC validation failed: " + "; ".join(errs))

        object_story_spec = {"page_id": page_id}
        if ig_user_id:
            object_story_spec["instagram_user_id"] = ig_user_id
        afs = self.build_pac_asset_feed_spec(
            headlines, bodies, description, feed_hash, story_hash, link_url, cta
        )
        kwargs = dict(name=name, object_story_spec=json.dumps(object_story_spec),
                      asset_feed_spec=json.dumps(afs))
        if validate_only:
            kwargs["execution_options"] = json.dumps(["validate_only"])
        resp = self.post(f"{self.account_id}/adcreatives", **kwargs)
        return resp.get("id", resp)

    # ---------- creative: VIDEO (push video as VIDEO — never a static frame) ----------
    VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".avi")

    @classmethod
    def media_type_of(cls, path: str) -> str:
        """'video' or 'image' from extension — router so a video never falls into the image path."""
        return "video" if path.lower().endswith(cls.VIDEO_EXTS) else "image"

    @staticmethod
    def _assert_video_id(video_id) -> None:
        """A Meta video_id is a NUMERIC string. An image hash (32-hex) here means a static
        frame/screenshot is about to be pushed as if it were the video — fail LOUD instead."""
        if not (isinstance(video_id, str) and video_id.isdigit()):
            raise ValueError(
                f"video_id must be a numeric /advideos id, got {video_id!r}. This usually means an "
                "image hash (a static frame/screenshot) was passed where a video was expected. "
                "Upload the .mp4 via upload_video()/multi_aspect_upload.py first."
            )

    def create_video_creative(
        self, name: str, page_id: str, video_id: str, message: str, link_url: str,
        cta: str = "LEARN_MORE", *, thumbnail_hash: str | None = None,
        thumbnail_url: str | None = None, headline: str | None = None,
        ig_user_id: str | None = None, url_tags: str | None = None,
    ) -> str:
        """Single-VIDEO ad creative via object_story_spec.video_data. Returns creative_id.

        The correct 'video as video' primitive. A poster is REQUIRED by Meta: pass
        thumbnail_hash (upload_image of a poster) or thumbnail_url (the video's auto
        'picture' from list_videos). The poster is the THUMBNAIL only; video_id is the asset.
        """
        self._assert_video_id(video_id)
        if not (thumbnail_hash or thumbnail_url):
            raise ValueError("video_data needs a poster: pass thumbnail_hash or thumbnail_url")
        video_data = {
            "video_id": video_id,
            "message": message,
            "call_to_action": {"type": cta, "value": {"link": link_url}},
        }
        video_data["image_hash" if thumbnail_hash else "image_url"] = thumbnail_hash or thumbnail_url
        if headline:
            video_data["title"] = headline
        oss = {"page_id": page_id, "video_data": video_data}
        if ig_user_id:
            oss["instagram_user_id"] = ig_user_id
        kwargs = dict(name=name, object_story_spec=json.dumps(oss))
        if url_tags:
            kwargs["url_tags"] = url_tags
        return self.post(f"{self.account_id}/adcreatives", **kwargs)["id"]

    def create_image_creative(
        self, name: str, page_id: str, image_hash: str, message: str, link_url: str,
        cta: str = "LEARN_MORE", *, headline: str | None = None, description: str | None = None,
        ig_user_id: str | None = None, url_tags: str | None = None,
    ) -> str:
        """Single-IMAGE ad creative via object_story_spec.link_data — NON-dynamic (one image,
        one body). Use for a control ad or discrete copy variants that must attribute per-ad.
        Returns creative_id."""
        if not image_hash:
            raise ValueError("image_hash required")
        link_data = {
            "image_hash": image_hash,
            "message": message,
            "link": link_url,
            "call_to_action": {"type": cta, "value": {"link": link_url}},
        }
        if headline:
            link_data["name"] = headline
        if description:
            link_data["description"] = description
        oss = {"page_id": page_id, "link_data": link_data}
        if ig_user_id:
            oss["instagram_user_id"] = ig_user_id
        kwargs = dict(name=name, object_story_spec=json.dumps(oss))
        if url_tags:
            kwargs["url_tags"] = url_tags
        return self.post(f"{self.account_id}/adcreatives", **kwargs)["id"]

    @staticmethod
    def build_pac_asset_feed_spec_video(
        headlines: list[str], bodies: list[str], description: str,
        feed_video: dict, story_video: dict, link_url: str, cta: str = "LEARN_MORE",
    ) -> dict:
        """Multi-aspect VIDEO PAC asset_feed_spec — MIRRORS the proven image PAC exactly,
        swapping images→videos (video_label instead of image_label). 4:5 = feed, 9:16 = catch-all.
        feed_video/story_video = {"video_id", "thumbnail_hash" OR "thumbnail_url"}.

        ⚠️ Run once with create_pac_creative_video(..., validate_only=True) before trusting —
        Meta's video asset_feed_spec is stricter than the image one.
        """
        for v in (feed_video, story_video):
            MetaClient._assert_video_id(v["video_id"])
            if not (v.get("thumbnail_hash") or v.get("thumbnail_url")):
                raise ValueError("each video needs thumbnail_hash or thumbnail_url (poster)")
        L = {"title": "title", "body": "body", "desc": "desc", "url": "url",
             "vid_feed": "vid_feed", "vid_story": "vid_story"}
        common = {"body_label": {"name": L["body"]}, "title_label": {"name": L["title"]},
                  "description_label": {"name": L["desc"]}, "link_url_label": {"name": L["url"]}}

        def _vid(v, lbl):
            it = {"video_id": v["video_id"], "adlabels": [{"name": lbl}]}
            it["thumbnail_hash" if v.get("thumbnail_hash") else "thumbnail_url"] = (
                v.get("thumbnail_hash") or v["thumbnail_url"])
            return it

        return {
            "ad_formats": ["AUTOMATIC_FORMAT"], "optimization_type": "PLACEMENT",
            "titles": [{"text": t, "adlabels": [{"name": L["title"]}]} for t in headlines],
            "bodies": [{"text": b, "adlabels": [{"name": L["body"]}]} for b in bodies],
            "descriptions": [{"text": description, "adlabels": [{"name": L["desc"]}]}],
            "videos": [_vid(feed_video, L["vid_feed"]), _vid(story_video, L["vid_story"])],
            "link_urls": [{"website_url": link_url, "adlabels": [{"name": L["url"]}]}],
            "call_to_action_types": [cta],
            "asset_customization_rules": [
                {  # Rule 1 — feed placements get the 4:5 video
                    "customization_spec": {
                        "age_min": 18, "age_max": 65,
                        "publisher_platforms": ["facebook", "instagram"],
                        "facebook_positions": ["feed"],
                        "instagram_positions": ["stream", "explore", "explore_home", "profile_feed"],
                    },
                    "video_label": {"name": L["vid_feed"]}, **common, "priority": 1,
                },
                {  # Rule 2 — catch-all (story/reels/everything else) gets the 9:16 video
                    "customization_spec": {"age_min": 18, "age_max": 65},
                    "video_label": {"name": L["vid_story"]}, **common, "priority": 2,
                },
            ],
        }

    def create_pac_creative_video(
        self, name: str, page_id: str, headlines: list[str], bodies: list[str],
        description: str, feed_video: dict, story_video: dict, link_url: str,
        cta: str = "LEARN_MORE", ig_user_id: str | None = None, validate_only: bool = False,
    ):
        """VIDEO PAC adcreative from video_ids (never image hashes). Returns creative_id
        (or the raw validation response when validate_only=True — ALWAYS dry-run first)."""
        afs = self.build_pac_asset_feed_spec_video(
            headlines, bodies, description, feed_video, story_video, link_url, cta)
        oss = {"page_id": page_id}
        if ig_user_id:
            oss["instagram_user_id"] = ig_user_id
        kwargs = dict(name=name, object_story_spec=json.dumps(oss), asset_feed_spec=json.dumps(afs))
        if validate_only:
            kwargs["execution_options"] = json.dumps(["validate_only"])
        resp = self.post(f"{self.account_id}/adcreatives", **kwargs)
        return resp.get("id", resp)

    def create_video_creative_multitext(
        self, name: str, page_id: str, video_id: str, headlines: list[str],
        bodies: list[str], description: str, link_url: str, cta: str = "LEARN_MORE",
        *, thumbnail_hash: str | None = None, thumbnail_url: str | None = None,
        ig_user_id: str | None = None, url_tags: str | None = None,
        text_optimization: bool = True, dof: dict | None = None,
        validate_only: bool = False,
    ):
        """ONE video + a POOL of headlines/bodies/description as MULTIPLE TEXT OPTIONS on a
        NORMAL (non-Dynamic-Creative) ad — the video analog of create_image_creative_multitext.
        Same working v25 recipe: video + poster + link + CTA live in `object_story_spec.video_data`;
        text pools live in `asset_feed_spec` with `optimization_type: DEGREES_OF_FREEDOM` and NO
        `ad_formats`/`videos`/`link_urls`. Non-DC → the ad set stays `is_dynamic_creative=false` and
        holds many such ads (mixed with image ads). Guarded against a screenshot hash in video_id.

        text_optimization=True (default) attaches the compliance-safe DOF bundle
        (`advantage_plus_creative:OPT_OUT` — Meta does NOT re-edit the burned creative — plus
        `text_optimizations:OPT_IN`). Without this bundle the multi-text still DELIVERS but Ads
        Manager renders only the first primary text; with it the "+N options" editor shows all
        (matches what Ads Manager produces for UI-made multi-text ads). Never OPT_IN advantage_plus_creative on a
        compliance-burned creative — it lets Meta crop/animate/overlay the image."""
        self._assert_video_id(video_id)
        if not (thumbnail_hash or thumbnail_url):
            raise ValueError("video needs a poster: thumbnail_hash or thumbnail_url")
        if not (1 <= len(headlines) <= 5 and 1 <= len(bodies) <= 5):
            raise ValueError("need 1-5 headlines and 1-5 bodies")
        video_data = {
            "video_id": video_id,
            "call_to_action": {"type": cta, "value": {"link": link_url}},
        }
        video_data["image_hash" if thumbnail_hash else "image_url"] = thumbnail_hash or thumbnail_url
        oss = {"page_id": page_id, "video_data": video_data}
        if ig_user_id:
            oss["instagram_user_id"] = ig_user_id
        afs = {
            "titles": [{"text": t} for t in headlines],
            "bodies": [{"text": b} for b in bodies],
            "descriptions": [{"text": description}],
            "optimization_type": "DEGREES_OF_FREEDOM",
        }
        kwargs = dict(name=name, object_story_spec=json.dumps(oss), asset_feed_spec=json.dumps(afs))
        if text_optimization:
            kwargs["degrees_of_freedom_spec"] = json.dumps(dof or self._TEXT_OPT_DOF)
        if url_tags:
            kwargs["url_tags"] = url_tags
        if validate_only:
            kwargs["execution_options"] = json.dumps(["validate_only"])
        resp = self.post(f"{self.account_id}/adcreatives", **kwargs)
        return resp.get("id", resp)

    def create_image_creative_multitext(
        self, name: str, page_id: str, image_hash: str, headlines: list[str],
        bodies: list[str], description: str, link_url: str, cta: str = "LEARN_MORE",
        *, ig_user_id: str | None = None, url_tags: str | None = None, dof: dict | None = None,
        text_optimization: bool = True, validate_only: bool = False,
    ):
        """ONE image + a POOL of headlines/bodies/description as MULTIPLE TEXT OPTIONS on a
        NORMAL (non-Dynamic-Creative) ad — the API equivalent of the Ads Manager "+ Add option"
        under Primary Text / Headline / Description. Meta optimizes text per person.

        The working v25 recipe (reverse-engineered from live UI ads + validated 2026-07-09):
          - media + link + CTA live in `object_story_spec.link_data` (the single image),
          - the text pools live in `asset_feed_spec` with `optimization_type: DEGREES_OF_FREEDOM`
            and NO `ad_formats` / `images` / `link_urls` / `call_to_action_types`,
          - `text_optimization=True` attaches the compliance-safe DOF bundle so Ads Manager renders
            the "+N options" editor (advantage_plus_creative OPT_OUT keeps the burned image intact;
            text_optimizations OPT_IN). Do NOT hand-set the deprecated `standard_enhancements` key
            (→ 3858504); this bundle avoids it.
        This is NON-DC: the ad set stays `is_dynamic_creative=false` and can hold MANY such ads.
        (The old asset_feed_spec-with-ad_formats shape was auto-classified as Dynamic Creative →
        error 1885998. Don't reintroduce it.) Returns creative_id (or validation response)."""
        if not image_hash:
            raise ValueError("image_hash required")
        if not (1 <= len(headlines) <= 5 and 1 <= len(bodies) <= 5):
            raise ValueError("need 1-5 headlines and 1-5 bodies")
        oss = {
            "page_id": page_id,
            "link_data": {
                "image_hash": image_hash,
                "link": link_url,
                "call_to_action": {"type": cta, "value": {"link": link_url}},
            },
        }
        if ig_user_id:
            oss["instagram_user_id"] = ig_user_id
        afs = {
            "titles": [{"text": t} for t in headlines],
            "bodies": [{"text": b} for b in bodies],
            "descriptions": [{"text": description}],
            "optimization_type": "DEGREES_OF_FREEDOM",
        }
        kwargs = dict(name=name, object_story_spec=json.dumps(oss), asset_feed_spec=json.dumps(afs))
        if text_optimization:
            kwargs["degrees_of_freedom_spec"] = json.dumps(dof or self._TEXT_OPT_DOF)
        if url_tags:
            kwargs["url_tags"] = url_tags
        if validate_only:
            kwargs["execution_options"] = json.dumps(["validate_only"])
        resp = self.post(f"{self.account_id}/adcreatives", **kwargs)
        return resp.get("id", resp)

    def create_ad(self, name: str, adset_id: str, creative_id: str, status: str = "PAUSED") -> str:
        """Attach a creative to an ad set as a PAUSED ad. Returns ad_id."""
        resp = self.post(
            f"{self.account_id}/ads",
            name=name,
            adset_id=adset_id,
            creative=json.dumps({"creative_id": creative_id}),
            status=status,
        )
        return resp["id"]

    # ---------- updates ----------
    def update_status(self, object_id: str, status: str) -> dict:
        """Status: PAUSED, ACTIVE, ARCHIVED, DELETED."""
        return self.post(object_id, status=status)

    def update_budget(self, object_id: str, daily_budget_cents: int) -> dict:
        return self.post(object_id, daily_budget=daily_budget_cents)

    def rename(self, object_id: str, name: str) -> dict:
        return self.post(object_id, name=name)

    # ---------- utilities ----------
    @staticmethod
    def split_zips(zips: list[str], max_per: int = 2500) -> list[list[str]]:
        """Split a zip list into chunks of at most max_per."""
        n = -(-len(zips) // max_per)  # ceil
        k, m = divmod(len(zips), n)
        return [zips[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n)]


TOKEN_HELP = (
    "No Meta access token found. Set the FACEBOOK_TOKEN environment variable, "
    "or put FACEBOOK_TOKEN=... in a .env file (path in $META_ENV, or ./.env in the "
    "working directory), or pass token= explicitly. See 'Bring your own token' in the README."
)


def _read_env_file(env_path: str) -> str | None:
    """Return FACEBOOK_TOKEN (or ACCESS_TOKEN) from a .env file, or None."""
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):]
                for var in ("FACEBOOK_TOKEN=", "ACCESS_TOKEN="):
                    if line.startswith(var):
                        return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    except FileNotFoundError:
        return None
    return None


def resolve_token(env_path: str | None = None) -> str | None:
    """Find a Meta token. Order: FACEBOOK_TOKEN / ACCESS_TOKEN env var ->
    explicit env_path -> $META_ENV -> ./.env. Returns None if nothing is found."""
    for var in ("FACEBOOK_TOKEN", "ACCESS_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    for path in (env_path, os.environ.get("META_ENV"), ".env"):
        if path:
            tok = _read_env_file(path)
            if tok:
                return tok
    return None


def load_token_from_env_file(env_path: str | None = None) -> str:
    """Like resolve_token(), but raises a generic error when no token is found."""
    tok = resolve_token(env_path)
    if not tok:
        raise RuntimeError(TOKEN_HELP)
    return tok
