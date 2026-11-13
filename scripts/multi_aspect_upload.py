#!/usr/bin/env python3
"""multi_aspect_upload.py — upload multi-aspect video sets to a Meta ad account.

What it does
------------
Walks an input folder and, for every ad, uploads its three aspect-ratio videos
(4:5, 9:16, 1:1) to the Meta ad account's video library, then writes a manifest
mapping each ad to its three returned video_ids. Those ids plug straight into a
Placement Asset Customization (PAC) creative.

File naming convention
----------------------
Files are grouped by a shared prefix and three suffixes:
    <prefix>_4x5.mp4
    <prefix>_9x16.mp4
    <prefix>_1x1.mp4
Each prefix becomes one entry in the manifest (label = prefix by default). The
smart_crop_video.py script produces exactly these suffixes.

Config (environment)
--------------------
    ACCESS_TOKEN      Meta Graph API access token (REQUIRED)
    AD_ACCOUNT_ID     e.g. act_<your-id> (or pass --account)

Usage
-----
    export ACCESS_TOKEN=...
    export AD_ACCOUNT_ID=act_XXXXXXXXX
    python3 multi_aspect_upload.py --input-dir ./out

Note: the Meta /advideos endpoint silently fails for very large files (>~120MB).
Compress to ~4 Mbps first (smart_crop_video.py already does this).
"""
import os
import json
import time
import argparse
from pathlib import Path

import requests

META_API_VERSION = "v25.0"
ASPECTS = [("4x5", "_4x5.mp4"), ("9x16", "_9x16.mp4"), ("1x1", "_1x1.mp4")]


def resolve_token():
    """FACEBOOK_TOKEN / ACCESS_TOKEN env var -> $META_ENV -> ./.env. See meta_client.resolve_token."""
    from meta_client import resolve_token as _resolve
    return _resolve()


def discover_video_sets(input_dir):
    """Group files by shared prefix into {prefix: {aspect: path|None}}.

    A prefix is any filename stem with one of the known aspect suffixes stripped.
    """
    input_dir = Path(input_dir)
    prefixes = set()
    for f in input_dir.glob("*.mp4"):
        for _, suffix in ASPECTS:
            if f.name.endswith(suffix):
                prefixes.add(f.name[: -len(suffix)])
                break
    sets = {}
    for prefix in sorted(prefixes):
        entry = {}
        for aspect, suffix in ASPECTS:
            candidate = input_dir / f"{prefix}{suffix}"
            entry[aspect] = candidate if candidate.exists() else None
        sets[prefix] = entry
    return sets


def upload_video(account_id, token, fpath):
    """Upload one video to /{account}/advideos. Returns video_id or None."""
    fname = fpath.name
    sz = fpath.stat().st_size / 1024 / 1024
    print(f"  Uploading {fname} ({sz:.1f}MB)...", end=" ", flush=True)
    try:
        with open(fpath, "rb") as f:
            r = requests.post(
                f"https://graph.facebook.com/{META_API_VERSION}/{account_id}/advideos",
                files={"source": (fname, f, "video/mp4")},
                data={"name": fname.replace(".mp4", ""), "access_token": token},
                timeout=300,
            )
        j = r.json()
        vid = j.get("id")
        if vid:
            print(f"ok {vid}")
            return vid
        print(f"FAIL {j}")
        return None
    except Exception as e:
        print(f"FAIL {e}")
        return None


def main():
    ap = argparse.ArgumentParser(
        description="Upload multi-aspect (4:5 + 9:16 + 1:1) video sets to a Meta ad account.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--account",
        default=os.environ.get("AD_ACCOUNT_ID"),
        help="Meta ad account id (act_XXXX...). Defaults to $AD_ACCOUNT_ID.",
    )
    ap.add_argument("--input-dir", default="./out", help="Folder containing the *_4x5/_9x16/_1x1.mp4 files")
    ap.add_argument("--manifest", default=None, help="JSON manifest path (default: <input-dir>/multi_aspect_manifest.json)")
    args = ap.parse_args()

    token = resolve_token()
    if not token:
        from meta_client import TOKEN_HELP
        ap.error(TOKEN_HELP)
    if not args.account:
        ap.error("Ad account id required — pass --account or set AD_ACCOUNT_ID.")

    input_dir = Path(args.input_dir)
    manifest_path = Path(args.manifest) if args.manifest else input_dir / "multi_aspect_manifest.json"

    sets = discover_video_sets(input_dir)
    if not sets:
        print(f"No *_4x5/_9x16/_1x1.mp4 sets found in {input_dir}")
        return

    manifest = {}
    for label, files in sets.items():
        entry = {}
        for aspect, _ in ASPECTS:
            fpath = files[aspect]
            if fpath is None:
                print(f"  missing {label}{[s for a, s in ASPECTS if a == aspect][0]}")
                entry[aspect] = None
                continue
            entry[aspect] = upload_video(args.account, token, fpath)
            time.sleep(0.5)  # gentle pacing — rapid retries extend Meta rate-limit cooldowns
        manifest[label] = entry

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    ok = sum(1 for v in manifest.values() if all(v.values()))
    total = len(manifest)
    print(f"\n=== {ok}/{total} fully uploaded (all 3 aspects) | manifest: {manifest_path} ===")


if __name__ == "__main__":
    main()
