"""
LLM caller with automatic fallback: Gemini models first, then Groq models.
Uses plain requests (no SDK needed). Returns the reply as a plain string.

Order of attempts:
  1. Each model in GEMINI_MODELS (default: gemini-2.5-flash, gemini-2.5-flash-lite)
  2. Each model in GROQ_MODELS   (default: qwen/qwen3.8-27b, llama-3.3-70b-versatile)

Rules:
- Temporary errors (500/502/503) are retried a few times with growing waits.
- 429 (quota), 404 (bad model name), 400 and others are NOT retried: move to the next model.
- Groq is skipped automatically if GROQ_API_KEY is not set.
- Keys are read from environment variables only, never from files.

Override the order from the workflow env, e.g.
  GEMINI_MODELS: "gemini-2.5-flash-lite,gemini-2.5-flash"
  GROQ_MODELS:   "qwen/qwen3.8-27b,llama-3.3-70b-versatile"
Model names change; check ai.google.dev and console.groq.com for what is currently available.
"""
import os
import re
import time

import requests


def _models(var, default):
    return [m.strip() for m in os.getenv(var, default).split(",") if m.strip()]


GEMINI_MODELS = _models("GEMINI_MODELS", "gemini-2.5-flash,gemini-2.5-flash-lite")
GROQ_MODELS = _models("GROQ_MODELS", "qwen/qwen3.8-27b,llama-3.3-70b-versatile")


def _try_gemini(model, prompt, json_mode):
    key = os.environ["GEMINI_API_KEY"]
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    last = ""
    for wait in (5, 15, 30):
        try:
            r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=60)
        except requests.RequestException as e:
            last = f"network error ({type(e).__name__})"
            print(f"Gemini {model}: {last}")
            time.sleep(wait)
            continue
        if r.status_code == 200:
            try:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"], ""
            except (KeyError, IndexError):
                return None, "empty or blocked response"
        last = f"HTTP {r.status_code} {r.text[:150]}"
        print(f"Gemini {model}: {last}")
        if r.status_code in (429, 500, 503):
            time.sleep(wait)
            continue
        break  # 429 / 404 / 400: go to next model
    return None, last


def _try_groq(model, prompt, json_mode):
    key = os.environ["GROQ_API_KEY"]
    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}  # prompt must mention "JSON"
    last = ""
    for wait in (5, 15):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
                timeout=90,
            )
        except requests.RequestException as e:
            last = f"network error ({type(e).__name__})"
            print(f"Groq {model}: {last}")
            time.sleep(wait)
            continue
        if r.status_code == 200:
            try:
                text = r.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                return None, "empty response"
            # Reasoning models (e.g. Qwen3) may include <think>...</think>; remove it.
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            return (text or None), ("" if text else "empty after cleanup")
        last = f"HTTP {r.status_code} {r.text[:150]}"
        print(f"Groq {model}: {last}")
        if r.status_code in (429, 500, 502, 503):
            time.sleep(wait)
            continue
        break  # 404 / 400: go to next model
    return None, last


def call_gemini(prompt, json_mode=False):
    """Name kept for compatibility: tries Gemini, then Groq. Returns text."""
    last = "no models configured"

    for model in GEMINI_MODELS:
        text, err = _try_gemini(model, prompt, json_mode)
        if text:
            print(f"Used Gemini model: {model}")
            return text
        last = f"Gemini {model}: {err}"

    if os.getenv("GROQ_API_KEY"):
        print("All Gemini models failed. Trying Groq...")
        for model in GROQ_MODELS:
            text, err = _try_groq(model, prompt, json_mode)
            if text:
                print(f"Used Groq model: {model}")
                return text
            last = f"Groq {model}: {err}"
    else:
        print("GROQ_API_KEY not set; skipping Groq fallback.")

    raise RuntimeError(f"All models failed. Last error: {last}")