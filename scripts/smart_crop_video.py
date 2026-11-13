#!/usr/bin/env python3
"""smart_crop_video.py — smart-crop portrait (9:16) video ads to 4:5 + 1:1.

What it does
------------
Takes 1080x1920 portrait videos and produces the two extra aspect ratios Meta
needs for full placement coverage:
  - 4:5  (1080x1350) for Feed
  - 1:1  (1080x1080) for square placements
The original 9:16 is copied through untouched (use it for Story / Reels).

How the crop is decided
-----------------------
1. PRIMARY (deterministic, free, no API key needed): OpenCV Haar face detection.
   A handful of frames are sampled across the video, the largest face in each is
   detected, and their union (top-most / bottom-most face edge) anchors a TIGHT
   crop with ~8% headspace above the head. Face visibility wins.
2. FALLBACK (optional): burned-in caption detection. If GEMINI_API_KEY is set,
   Gemini locates any burned-in caption / lower-third so the crop can try to keep
   it in frame as a SOFT constraint (face always wins). With no key set, the crop
   relies on face anchoring alone (and a blind centre crop if no face is found).

Encoding: libx264, ~4 Mbps cap (maxrate 4M / bufsize 8M), CRF 23, faststart.

Usage
-----
    export GEMINI_API_KEY=...                 # OPTIONAL — caption fallback only
    python3 smart_crop_video.py --src ./input_videos --out-dir ./out

Requires ffmpeg on PATH (brew install ffmpeg / apt install ffmpeg) and
opencv-python.
"""
import os
import json
import subprocess
import base64
import argparse
from pathlib import Path

import cv2
import numpy as np

# Gemini is a FALLBACK only. Face anchoring runs without any key.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"
CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

SRC_H = 1920
TARGET_45_H = 1350  # 4:5 at 1080 wide
TARGET_11_H = 1080  # 1:1 at 1080 wide
HEADSPACE_RATIO = 0.08  # 8% of crop height above face_top — TIGHT framing

PROMPT_CAPTION = """This is a frame from a portrait video ad. Find any BURNED-IN TEXT (subtitles/captions/lower-thirds/headlines burned into pixels — NOT Meta UI overlays).
Return JSON normalized [0..1]:
{"text_overlay": {"top": <float>, "bottom": <float>, "text": "<>"} or null,
 "scene_type": "talking_head | product_demo | b_roll | text_only | mixed"}"""


def extract_frames(src_mp4, percentages=(0.10, 0.30, 0.50, 0.70, 0.90)):
    cap = cv2.VideoCapture(src_mp4)
    fps = cap.get(cv2.CAP_PROP_FPS)
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = n_frames / fps if fps > 0 else 0
    out = []
    for pct in percentages:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n_frames * pct))
        ok, frame = cap.read()
        if ok:
            out.append((pct, frame))
    cap.release()
    return out, duration


def detect_largest_face(frame_bgr):
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    faces = CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return {"top": int(y), "bottom": int(y + h), "h": int(h)}


def gemini_caption_detect(frame_bgr):
    """Optional burned-in caption detection. Returns (caption_bbox_or_None, scene_type).

    No-ops (returns None) when GEMINI_API_KEY is not set.
    """
    if not GEMINI_API_KEY:
        return None, "unknown"
    import requests  # imported lazily — only needed for the fallback path

    ok, jpg = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return None, "unknown"
    b64 = base64.b64encode(jpg.tobytes()).decode()
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": PROMPT_CAPTION},
                            {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                        ]
                    }
                ],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
            },
            timeout=30,
        ).json()
        a = json.loads(r["candidates"][0]["content"]["parts"][0]["text"])
        return a.get("text_overlay"), a.get("scene_type", "unknown")
    except Exception:
        return None, "error"


def compute_video_crop(face_union, caption_bbox, target_h, src_h=SRC_H):
    """Tight face anchor with caption soft-constraint.

    Priority:
    1. Face fully visible (top_min and bottom_max both inside crop).
    2. Minimize headspace above face (~8% of crop height).
    3. Caption inside crop (anywhere — top/middle/bottom all OK).
    4. If face + caption can't both fit -> face wins, warn about caption.
    """
    warnings = []
    if face_union is None and caption_bbox is None:
        y0 = (src_h - target_h) // 2
        return {"y0": y0, "y1": y0 + target_h, "mode": "blind-center", "warnings": ["no face/caption detected"]}
    if face_union is None:
        # Caption only — center on caption.
        c_top = int(float(caption_bbox["top"]) * src_h)
        c_bot = int(float(caption_bbox["bottom"]) * src_h)
        cap_mid = (c_top + c_bot) // 2
        y0 = max(0, min(src_h - target_h, cap_mid - target_h // 2))
        return {"y0": y0, "y1": y0 + target_h, "mode": "caption-centered", "warnings": warnings}

    # TIGHT face anchor — small headspace above face_top_min.
    margin_top = int(target_h * HEADSPACE_RATIO)
    y0 = face_union["top_min"] - margin_top

    # Check face_bottom fits.
    if y0 + target_h < face_union["bottom_max"]:
        # Face too tall for crop — shift down to keep mouth visible (bottom-bias).
        y0 = face_union["bottom_max"] - target_h
        actual_headspace = face_union["top_min"] - y0
        if actual_headspace < 30:
            warnings.append(
                f"face union {face_union['top_min']}-{face_union['bottom_max']} "
                f"({face_union['bottom_max'] - face_union['top_min']}px) too tall for {target_h}px crop — top of head clipped"
            )

    # Caption check (soft constraint — try to fit, otherwise face wins).
    if caption_bbox:
        c_top = int(float(caption_bbox["top"]) * src_h)
        c_bot = int(float(caption_bbox["bottom"]) * src_h)
        y1 = y0 + target_h
        cap_in_crop = c_top >= y0 and c_bot <= y1
        if not cap_in_crop:
            if c_bot > y1:
                # Caption below crop end — try shift DOWN, keep face visible.
                shift = c_bot - y1
                max_shift_down = max(0, face_union["top_min"] - 30 - y0)  # leave 30px min headspace
                applied = min(shift, max_shift_down)
                y0 += applied
                if applied < shift:
                    warnings.append(
                        f"caption (y={c_top}-{c_bot}) below crop — shifted {applied}/{shift}px, partial clip"
                    )
            elif c_top < y0:
                # Caption above crop start — try shift UP without clipping face bottom.
                shift = y0 - c_top
                max_shift_up_safe = max(0, y0 + target_h - face_union["bottom_max"])
                applied = min(shift, max_shift_up_safe)
                y0 -= applied
                if applied < shift:
                    warnings.append(f"caption above crop — shifted {applied}/{shift}px")

    # Final clamp.
    y0 = max(0, min(src_h - target_h, y0))
    actual_headspace = face_union["top_min"] - y0
    return {
        "y0": y0,
        "y1": y0 + target_h,
        "mode": "tight-face",
        "headspace_above_face": actual_headspace,
        "warnings": warnings,
    }


def make_ffmpeg_crop(src, dst, target_w, target_h, y0):
    """Re-encode a vertical crop band with a ~4 Mbps libx264 ceiling."""
    return subprocess.run(
        [
            "ffmpeg", "-y", "-i", src,
            "-vf", f"crop={target_w}:{target_h}:0:{y0}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-maxrate", "4M", "-bufsize", "8M",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", dst,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )


def process_video(src_path, dst_dir):
    src = str(src_path)
    print(f"\n=== {src_path.name} ===")
    frames, duration = extract_frames(src)
    face_results = [detect_largest_face(frame) for _, frame in frames]
    n_with_face = sum(1 for f in face_results if f)
    print(f"  duration: {duration:.1f}s, faces on {n_with_face}/{len(frames)} frames")
    face_union = None
    if n_with_face > 0:
        valid = [f for f in face_results if f]
        face_union = {
            "top_min": min(f["top"] for f in valid),
            "bottom_max": max(f["bottom"] for f in valid),
            "drift_y_px": max(f["top"] for f in valid) - min(f["top"] for f in valid),
        }
        print(f"  face_union: top={face_union['top_min']} bot={face_union['bottom_max']} drift={face_union['drift_y_px']}")
    middle_frame = frames[len(frames) // 2][1] if frames else None
    caption, scene = (None, "unknown")
    if middle_frame is not None:
        caption, scene = gemini_caption_detect(middle_frame)
    if caption:
        print(f"  caption: {caption}")
    crop_45 = compute_video_crop(face_union, caption, TARGET_45_H)
    crop_11 = compute_video_crop(face_union, caption, TARGET_11_H)
    print(f"  4:5: y={crop_45['y0']}-{crop_45['y1']} headspace={crop_45.get('headspace_above_face', '-')} warn={crop_45['warnings']}")
    print(f"  1:1: y={crop_11['y0']}-{crop_11['y1']} headspace={crop_11.get('headspace_above_face', '-')} warn={crop_11['warnings']}")
    stem = src_path.stem
    out_45 = dst_dir / f"{stem}_4x5.mp4"
    out_11 = dst_dir / f"{stem}_1x1.mp4"
    out_916 = dst_dir / f"{stem}_9x16.mp4"
    if not out_916.exists():
        # Keep the original 9:16 untouched (stream copy, no re-encode).
        subprocess.run(["ffmpeg", "-y", "-i", src, "-c", "copy", str(out_916)], capture_output=True, timeout=60)
    make_ffmpeg_crop(src, str(out_45), 1080, TARGET_45_H, crop_45["y0"])
    make_ffmpeg_crop(src, str(out_11), 1080, TARGET_11_H, crop_11["y0"])
    return {
        "duration": duration,
        "scene_type": scene,
        "face_union": face_union,
        "caption": caption,
        "crop_4x5": crop_45,
        "crop_1x1": crop_11,
        "out_4x5": str(out_45),
        "out_1x1": str(out_11),
        "out_9x16": str(out_916),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Smart-crop 1080x1920 portrait videos to 4:5 + 1:1 (keeps original 9:16).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--src", default="./input_videos", help="Folder of 1080x1920 .mp4 source videos")
    ap.add_argument("--out-dir", default="./out", help="Output folder for the 4x5 / 1x1 / 9x16 mp4s")
    ap.add_argument("--manifest", default=None, help="JSON manifest path (default: <out-dir>/smart_crop_video_manifest.json)")
    ap.add_argument("--files", nargs="*", help="Process only these basenames (default: all .mp4 in --src)")
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.out_dir)
    dst.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(args.manifest) if args.manifest else dst / "smart_crop_video_manifest.json"

    if not GEMINI_API_KEY:
        print("note: GEMINI_API_KEY not set — caption fallback disabled, using face anchoring only.")

    files = sorted(src.glob("*.mp4"))
    if args.files:
        files = [f for f in files if f.name in args.files]
    manifest = {}
    for f in files:
        try:
            manifest[f.name] = process_video(f, dst)
        except Exception as e:
            print(f"  FAIL: {e}")
            manifest[f.name] = {"error": str(e)}
    with open(manifest_path, "w") as fp:
        json.dump(manifest, fp, indent=2, default=str)
    print(f"\n=== {len(manifest)} videos processed, manifest: {manifest_path} ===")


if __name__ == "__main__":
    main()
