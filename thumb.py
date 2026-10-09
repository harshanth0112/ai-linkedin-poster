"""
Thumbnail maker with fallbacks. Always produces a 1200x627 JPEG so a post never fails over an image.

Order (a source is skipped if its key is not set):
  1. Gemini image model(s) in GEMINI_IMAGE_MODELS (GEMINI_API_KEY)
  2. Cloudflare Workers AI (CF_ACCOUNT_ID + CF_API_TOKEN, optional CF_IMAGE_MODEL)
  3. Hugging Face Inference (HF_TOKEN, optional HF_IMAGE_MODEL)
  4. Pollinations.ai (free, no key)
  5. Plain gradient background (guaranteed)
Then a headline is drawn on top with Pillow (AI models draw text badly, so we never ask them to).

Env options (all optional):
  GEMINI_IMAGE_MODELS  default "gemini-2.5-flash-image" (run list_models.py and look for names containing "image")
  IMAGE_STYLE          style words added to every prompt
  THUMB_TEXT           "1" (default) draws the headline, "0" leaves the image text-free
"""
import base64
import io
import os
import textwrap
import time
import urllib.parse

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1200, 627
STYLE = os.getenv(
    "IMAGE_STYLE",
    "modern digital illustration, clean composition, soft gradients, "
    "no text, no letters, no logos, no watermark, no real people",
)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # GitHub Actions (Ubuntu)
    "C:\\Windows\\Fonts\\arialbd.ttf",                          # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",       # macOS
]


# ---------- Sources ----------
def _gemini_image(prompt):
    models = [
        m.strip() for m in os.getenv("GEMINI_IMAGE_MODELS", "gemini-2.5-flash-image").split(",") if m.strip()
    ]
    if not models:
        print("Skipping Gemini image (GEMINI_IMAGE_MODELS is empty)")
        return None
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": f"{prompt}. {STYLE}. Wide landscape 1.91:1 composition."}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
        try:
            r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=120)
        except requests.RequestException as e:
            print(f"Image {model}: network error ({type(e).__name__})")
            continue
        if r.status_code != 200:
            print(f"Image {model}: HTTP {r.status_code} {r.text[:120]}")
            continue
        try:
            for part in r.json()["candidates"][0]["content"]["parts"]:
                data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
                if data:
                    print(f"Image from Gemini model: {model}")
                    return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
        except Exception as e:
            print(f"Image {model}: could not read response ({type(e).__name__})")
            continue
        print(f"Image {model}: response had no image")
    return None


def _pollinations(prompt):
    if os.getenv("ENABLE_POLLINATIONS", "1") == "0":
        return None
    q = urllib.parse.quote(f"{prompt}, {STYLE}")
    url = f"https://image.pollinations.ai/prompt/{q}?width={W}&height={H}&nologo=true&seed={int(time.time())}"
    for _ in range(2):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                print("Image from Pollinations")
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception as e:
            print(f"Pollinations: {type(e).__name__}")
        time.sleep(8)
    return None


def _cloudflare(prompt):
    """Cloudflare Workers AI (free daily allowance). Needs CF_ACCOUNT_ID and CF_API_TOKEN."""
    acct, tok = os.getenv("CF_ACCOUNT_ID"), os.getenv("CF_API_TOKEN")
    if not (acct and tok):
        return None
    model = os.getenv("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {tok}"},
            json={"prompt": f"{prompt}. {STYLE}"[:1900], "steps": 4},
            timeout=120,
        )
        if r.status_code != 200:
            print(f"Cloudflare {model}: HTTP {r.status_code} {r.text[:120]}")
            return None
        data = r.json()["result"]["image"]
        print("Image from Cloudflare Workers AI")
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
    except Exception as e:
        print(f"Cloudflare: {type(e).__name__}")
        return None


def _huggingface(prompt):
    """Hugging Face Inference API (free monthly credits). Needs HF_TOKEN."""
    tok = os.getenv("HF_TOKEN")
    if not tok:
        return None
    model = os.getenv("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")
    url = f"https://router.huggingface.co/hf-inference/models/{model}"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {tok}"},
            json={"inputs": f"{prompt}. {STYLE}"},
            timeout=120,
        )
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            print(f"Image from Hugging Face: {model}")
            return Image.open(io.BytesIO(r.content)).convert("RGB")
        print(f"HuggingFace {model}: HTTP {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"HuggingFace: {type(e).__name__}")
    return None


def _ai_image(prompt):
    """Try each image source in order; the first one that returns an image wins."""
    for source in (_gemini_image, _cloudflare, _huggingface, _pollinations):
        img = source(prompt)
        if img is not None:
            return img
    return None


def _fallback_background():
    print("Image: using gradient fallback")
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    top, bottom = (18, 28, 64), (96, 52, 140)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img


# ---------- Headline overlay ----------
def _font(size):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _overlay_title(img, title):
    title = " ".join(title.split())
    if len(title) > 90:
        title = title[:87].rstrip() + "..."
    img = img.convert("RGBA")

    # Dark gradient over the lower part so the text is always readable.
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    start = int(H * 0.40)
    for y in range(start, H):
        alpha = int(215 * (y - start) / (H - start))
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, shade)

    d = ImageDraw.Draw(img)
    size = 56
    lines = textwrap.wrap(title, width=30, max_lines=3, placeholder="...")
    font = _font(size)
    line_h = int(size * 1.25)
    y = H - 50 - line_h * len(lines)
    for line in lines:
        d.text((60 + 2, y + 2), line, font=font, fill=(0, 0, 0, 200))  # soft shadow
        d.text((60, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h
    return img.convert("RGB")


def ai_background(prompt):
    """AI-generated image only (no gradient fallback, no text). Returns a PIL image or None."""
    return _ai_image(prompt)


# ---------- Public function ----------
def make_image(prompt, headline=None, path="image.jpg"):
    """Returns the saved image path. headline = short text drawn on the thumbnail (optional)."""
    img = _ai_image(prompt) or _fallback_background()
    img = ImageOps.fit(img, (W, H), method=Image.Resampling.LANCZOS)  # crop/resize to exactly 1200x627
    if headline and os.getenv("THUMB_TEXT", "1") == "1":
        img = _overlay_title(img, headline)
    img.save(path, "JPEG", quality=90)
    return path
