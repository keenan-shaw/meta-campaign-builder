#!/usr/bin/env python3
"""smart_crop_image.py — smart-crop portrait (9:16) ad creatives to 4:5 + 1:1.

What it does
------------
Takes 1080x1920 portrait images and produces two extra aspect ratios that Meta
needs for full placement coverage:
  - 4:5  (1080x1350) for Feed
  - 1:1  (1080x1080) for square placements
The original 9:16 image is left untouched (use it for Story / Reels).

How the crop is decided
-----------------------
1. PRIMARY (deterministic, free, no API key needed): pixel-based content
   envelope detection. The background colour is sampled from the four corners;
   every row that contains enough non-background pixels is "content". The crop is
   centred on the midpoint of the top-most and bottom-most content rows so the
   headline and CTA both stay in frame.
2. FALLBACK (optional): if the background is not a solid colour (photographic /
   gradient backgrounds), the deterministic path bails out. If GEMINI_API_KEY is
   set, Gemini is asked for the content envelope. If no key is set, the script
   prints a warning and falls back to a safe centre crop instead of crashing.

Usage
-----
    export GEMINI_API_KEY=...        # OPTIONAL — only used as a fallback
    python3 smart_crop_image.py --src ./input_images --out-dir ./out

Outputs land in <out-dir>/4x5 and <out-dir>/1x1, plus an HTML preview and a JSON
manifest in <out-dir>.
"""
import os
import json
import base64
import argparse
from pathlib import Path

from PIL import Image
import numpy as np

# Gemini is a FALLBACK only. The deterministic pixel path runs without any key.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"

SRC_W, SRC_H = 1080, 1920
TARGET_45_H = 1350  # 4:5 at 1080 wide
TARGET_11_H = 1080  # 1:1 at 1080 wide


def detect_background_color(arr_rgb):
    """Background = MEDIAN of corner pixels (40x40 patches in 4 corners).

    Corners on portrait ad creatives are nearly always pure background.
    Returns (bg_color[int,int,int], confidence[0..1]).
    """
    patches = [
        arr_rgb[:40, :40].reshape(-1, 3),
        arr_rgb[:40, -40:].reshape(-1, 3),
        arr_rgb[-40:, :40].reshape(-1, 3),
        arr_rgb[-40:, -40:].reshape(-1, 3),
    ]
    samples = np.vstack(patches).astype(int)
    # Median is robust to a corner happening to contain a non-bg pixel.
    bg_color = np.median(samples, axis=0).astype(int)
    # Confidence = fraction of corner samples within 30 RGB units of median.
    distances = np.linalg.norm(samples - bg_color, axis=1)
    bg_confidence = float((distances < 30).sum()) / len(distances)
    return bg_color, bg_confidence


def detect_content_envelope_pixel(img_path, threshold=25, min_row_width=50):
    """Find the topmost + bottommost rows that contain non-background content.

    Returns (content_top_y, content_bottom_y, bg_color, bg_confidence) or
    (None, None, bg_color, bg_confidence) if the background is not solid enough.
    """
    img = Image.open(img_path).convert("RGB")
    arr = np.array(img)
    bg_color, bg_conf = detect_background_color(arr)
    if bg_conf < 0.7:
        # Background not solid — bail out to the fallback path.
        return None, None, bg_color, bg_conf
    # Per-pixel distance from bg color.
    diff = np.linalg.norm(arr.astype(int) - bg_color, axis=2)
    non_bg = diff > threshold  # H x W bool
    # Per row: count non-bg pixels.
    row_counts = non_bg.sum(axis=1)
    # A row "has content" if at least min_row_width pixels are non-bg.
    has_content = row_counts >= min_row_width
    indices = np.where(has_content)[0]
    if len(indices) == 0:
        return None, None, bg_color, bg_conf
    return int(indices[0]), int(indices[-1]), bg_color.tolist(), bg_conf


PROMPT_FALLBACK = """Analyze this portrait ad creative. Return JSON with NORMALIZED Y-coordinates [0.0, 1.0]:
{
  "content_top": <float — Y where first visible text/photo/logo starts>,
  "content_bottom": <float — Y where last visible text/photo/CTA ends>,
  "elements_summary": "<list every visible text and graphic element with approx Y>"
}
Be PRECISE. Visually scan from top-down for the first non-background element, top-up for the last."""


def gemini_envelope_fallback(img_path):
    """Ask Gemini for the content envelope. Requires GEMINI_API_KEY.

    Returns (content_top_px, content_bottom_px, elements_summary).
    Raises on any failure so the caller can fall back to a centre crop.
    """
    import requests  # imported lazily — only needed for the fallback path

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    b64 = base64.b64encode(open(img_path, "rb").read()).decode()
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_API_KEY},
        json={
            "contents": [
                {
                    "parts": [
                        {"text": PROMPT_FALLBACK},
                        {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        },
        timeout=60,
    ).json()
    a = json.loads(r["candidates"][0]["content"]["parts"][0]["text"])
    return (
        int(float(a["content_top"]) * SRC_H),
        int(float(a["content_bottom"]) * SRC_H),
        a.get("elements_summary", ""),
    )


def safe_center_envelope():
    """Last-resort envelope when no detection is available — rule-of-thirds band.

    Centres content on the middle of the frame so a centre crop is produced.
    """
    return SRC_H // 3, SRC_H * 2 // 3


def compute_crop(content_top, content_bottom, target_h, src_h=SRC_H):
    """Pure envelope-center crop. NO mode switching.

    Find topmost content edge + bottommost content edge, center the crop on the
    midpoint, then clamp to the image and report any clipping.
    """
    content_mid = (content_top + content_bottom) // 2
    y0 = content_mid - target_h // 2
    y0 = max(0, min(src_h - target_h, y0))
    y1 = y0 + target_h
    fits_top = content_top >= y0
    fits_bot = content_bottom <= y1
    warnings = []
    if not fits_top:
        warnings.append(
            f"top of content (y={content_top}) ABOVE crop start (y={y0}) — clipped {y0 - content_top}px"
        )
    if not fits_bot:
        warnings.append(
            f"bottom of content (y={content_bottom}) BELOW crop end (y={y1}) — clipped {content_bottom - y1}px"
        )
    return {
        "y0": y0,
        "y1": y1,
        "warnings": warnings,
        "content_top": content_top,
        "content_bottom": content_bottom,
        "content_mid": content_mid,
    }


def render_preview_card(src_path, content_top, content_bottom, bg_info, crop_45, crop_11):
    name = Path(src_path).name
    img_b64 = base64.b64encode(open(src_path, "rb").read()).decode()
    env_overlay = (
        f'<div class="env-band" style="top:{content_top / SRC_H * 100:.2f}%;'
        f'height:{(content_bottom - content_top) / SRC_H * 100:.2f}%">'
        f'<span class="env-tag">CONTENT ENVELOPE y={content_top}-{content_bottom}</span></div>'
    )
    overlay_45 = (
        f'<div class="crop-overlay c45" style="top:{crop_45["y0"] / SRC_H * 100:.2f}%;'
        f'height:{(crop_45["y1"] - crop_45["y0"]) / SRC_H * 100:.2f}%">'
        f'<span class="crop-tag" style="background:#16a34a">4:5 y={crop_45["y0"]}-{crop_45["y1"]}</span></div>'
    )
    overlay_11 = (
        f'<div class="crop-overlay c11" style="top:{crop_11["y0"] / SRC_H * 100:.2f}%;'
        f'height:{(crop_11["y1"] - crop_11["y0"]) / SRC_H * 100:.2f}%">'
        f'<span class="crop-tag" style="background:#dc2626;right:4px;left:auto">1:1 y={crop_11["y0"]}-{crop_11["y1"]}</span></div>'
    )

    warn_45 = "<br>".join(crop_45["warnings"]) or "all content fits"
    warn_11 = "<br>".join(crop_11["warnings"]) or "all content fits"
    cls_45 = "danger" if crop_45["warnings"] else "clean"
    cls_11 = "danger" if crop_11["warnings"] else "clean"

    return f"""
<div class="card">
  <h3>{name}</h3>
  <div class="row">
    <div class="src-wrap">
      <div class="src">
        <img src="data:image/jpeg;base64,{img_b64}" />
        {env_overlay}
        {overlay_45}
        {overlay_11}
      </div>
      <div class="legend">
        <span class="lg" style="background:#fbbf24">CONTENT</span>
        <span class="lg" style="background:#16a34a">4:5</span>
        <span class="lg" style="background:#dc2626">1:1</span>
      </div>
    </div>
    <div class="info">
      <p><strong>Detection method:</strong> {bg_info}</p>
      <p><strong>Content envelope:</strong> y={content_top}-{content_bottom} (height {content_bottom - content_top}px)</p>
      <p><strong>Content midpoint:</strong> y={crop_45['content_mid']}</p>
      <h4>4:5 crop (1080x1350)</h4>
      <p>y = <strong>{crop_45['y0']} -&gt; {crop_45['y1']}</strong></p>
      <p class="warn {cls_45}">{warn_45}</p>
      <h4>1:1 crop (1080x1080)</h4>
      <p>y = <strong>{crop_11['y0']} -&gt; {crop_11['y1']}</strong></p>
      <p class="warn {cls_11}">{warn_11}</p>
    </div>
  </div>
</div>
"""


CSS = """
<style>
body { font-family: -apple-system, system-ui, sans-serif; background: #f3f4f6; margin: 0; padding: 20px; }
h1 { color: #111; }
.card { background: white; padding: 16px; margin-bottom: 24px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
.row { display: flex; gap: 24px; align-items: flex-start; }
.src-wrap { flex: 0 0 360px; }
.src { position: relative; width: 360px; aspect-ratio: 9/16; }
.src img { width: 100%; height: 100%; object-fit: cover; display: block; border: 1px solid #ccc; }
.env-band { position: absolute; left: 0; right: 0; border: 2px dashed #fbbf24; background: #fbbf2418; }
.env-tag { position: absolute; left: 0; top: -20px; background: #fbbf24; color: #1f2937; padding: 2px 6px; font-size: 10px; font-weight: bold; white-space: nowrap; }
.crop-overlay { position: absolute; left: 0; right: 0; border: 3px solid; pointer-events: none; }
.c45 { border-color: #16a34a; background: #16a34a14; }
.c11 { border-color: #dc2626; background: #dc262614; }
.crop-tag { position: absolute; top: 4px; left: 4px; color: white; padding: 2px 8px; font-size: 11px; font-weight: bold; border-radius: 3px; }
.legend { display: flex; gap: 6px; margin-top: 8px; }
.lg { color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: bold; }
.info { flex: 1; font-size: 13px; }
.info h4 { margin: 16px 0 6px; }
.warn { padding: 6px 10px; border-radius: 4px; font-size: 12px; }
.warn.clean { background: #d1fae5; color: #065f46; }
.warn.danger { background: #fee2e2; color: #991b1b; }
</style>
"""


def main():
    ap = argparse.ArgumentParser(
        description="Smart-crop 1080x1920 portrait creatives to 4:5 + 1:1 (keeps original 9:16).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--src", default="./input_images", help="Folder of 1080x1920 .jpg source images")
    ap.add_argument("--out-dir", default="./out", help="Output folder (4x5/ and 1x1/ created inside)")
    ap.add_argument("--preview", default=None, help="HTML preview path (default: <out-dir>/smart_crop_preview.html)")
    ap.add_argument("--manifest", default=None, help="JSON manifest path (default: <out-dir>/smart_crop_manifest.json)")
    args = ap.parse_args()

    src = Path(args.src)
    out_dir = Path(args.out_dir)
    dst_45 = out_dir / "4x5"
    dst_11 = out_dir / "1x1"
    dst_45.mkdir(parents=True, exist_ok=True)
    dst_11.mkdir(parents=True, exist_ok=True)
    preview_path = Path(args.preview) if args.preview else out_dir / "smart_crop_preview.html"
    manifest_path = Path(args.manifest) if args.manifest else out_dir / "smart_crop_manifest.json"

    if not GEMINI_API_KEY:
        print("note: GEMINI_API_KEY not set — fallback uses a safe centre crop for non-solid backgrounds.")

    cards = []
    manifest = {}
    for f in sorted(src.glob("*.jpg")):
        img = Image.open(f)
        if img.size != (SRC_W, SRC_H):
            continue
        print(f"  {f.name}...", end=" ", flush=True)
        # PRIMARY: pixel-based deterministic detection.
        c_top, c_bot, bg_color, bg_conf = detect_content_envelope_pixel(f)
        if c_top is None:
            # FALLBACK: Gemini if a key is present, otherwise a safe centre crop.
            try:
                c_top, c_bot, summary = gemini_envelope_fallback(f)
                method = f"Gemini fallback (bg confidence {bg_conf:.2f} < 0.7) — {summary[:80]}"
            except Exception as e:
                c_top, c_bot = safe_center_envelope()
                method = f"safe centre crop (bg confidence {bg_conf:.2f} < 0.7, no Gemini: {e})"
                print("(safe centre)", end=" ")
        else:
            method = f"pixel-detection (bg color rgb{tuple(bg_color)}, confidence {bg_conf:.2f})"

        crop_45 = compute_crop(c_top, c_bot, TARGET_45_H)
        crop_11 = compute_crop(c_top, c_bot, TARGET_11_H)
        out_45 = dst_45 / (f.stem + "_4x5.jpg")
        out_11 = dst_11 / (f.stem + "_1x1.jpg")
        img.convert("RGB").crop((0, crop_45["y0"], SRC_W, crop_45["y1"])).save(out_45, "JPEG", quality=92)
        img.convert("RGB").crop((0, crop_11["y0"], SRC_W, crop_11["y1"])).save(out_11, "JPEG", quality=92)
        manifest[f.name] = {
            "method": method,
            "content_top": c_top,
            "content_bottom": c_bot,
            "crop_4x5": crop_45,
            "crop_1x1": crop_11,
            "out_4x5": str(out_45),
            "out_1x1": str(out_11),
        }
        w45 = "warn" if crop_45["warnings"] else "ok"
        w11 = "warn" if crop_11["warnings"] else "ok"
        print(f"env={c_top}-{c_bot} | 4:5={crop_45['y0']}-{crop_45['y1']} {w45} | 1:1={crop_11['y0']}-{crop_11['y1']} {w11}")
        cards.append(render_preview_card(f, c_top, c_bot, method, crop_45, crop_11))

    with open(manifest_path, "w") as fp:
        json.dump(manifest, fp, indent=2)

    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Smart-Crop</title>{CSS}</head><body>
<h1>Smart-Crop — pure envelope (text top -&gt; text bottom -&gt; center)</h1>
<p>Detection: pixel-based on flat backgrounds (deterministic, &lt;50ms). Gemini / safe-centre fallback for photographic bg.</p>
<p><span style="color:#fbbf24;font-weight:bold">Yellow dashed band</span> = detected content envelope &middot; <span style="color:#16a34a;font-weight:bold">Green box</span> = 4:5 crop &middot; <span style="color:#dc2626;font-weight:bold">Red box</span> = 1:1 crop</p>
{"".join(cards)}
</body></html>"""
    with open(preview_path, "w") as f:
        f.write(html)
    print(f"\n=== {len(cards)} processed | preview: {preview_path} | manifest: {manifest_path} ===")


if __name__ == "__main__":
    main()
