"""
Drop-in Gemini caller with model fallback. Uses plain requests (no SDK needed).

- Retries only temporary errors (500/503), max 3 tries per model with growing waits.
- Never retries 429 (quota), 404 (bad model name) or 400: moves to the next model instead.
- Free-tier quota is counted per model, so a fallback model has its own daily allowance.
- Override the model order with an env var, e.g.
  GEMINI_MODELS="gemini-2.5-flash,gemini-2.5-flash-lite"
  (check ai.google.dev/gemini-api/docs/rate-limits for models available on your free tier).
"""
import os
import time

import requests

MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_MODELS", "gemini-2.0-flash-lite,gemini-2.5-flash-lite,gemini-2.0-flash").split(",")
    if m.strip()
]


def call_gemini(prompt, json_mode=False):
    """Returns the model's text reply as a plain string."""
    key = os.environ["GEMINI_API_KEY"]
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}

    last = "no models configured"
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for wait in (5, 15, 30):
            try:
                # Key goes in a header so it never appears in URLs or logs.
                r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=60)
            except requests.RequestException as e:
                last = f"{model}: network error ({type(e).__name__})"
                print("Gemini:", last)
                time.sleep(wait)
                continue

            if r.status_code == 200:
                try:
                    return r.json()["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    last = f"{model}: empty or blocked response"
                    print("Gemini:", last)
                    break  # try next model

            last = f"{model}: HTTP {r.status_code} {r.text[:150]}"
            print("Gemini:", last)
            if r.status_code in (500, 503):
                time.sleep(wait)  # temporary overload: wait and retry same model
                continue
            break  # 429 / 404 / 400: retrying won't help, go to next model

    raise RuntimeError(f"All Gemini models failed. Last error: {last}")
