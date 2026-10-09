"""List Gemini model ids available to this API key. Put a working text model in GEMINI_MODELS."""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

key = os.getenv("GEMINI_API_KEY")
if not key:
    raise SystemExit("GEMINI_API_KEY is not set")

r = requests.get(
    "https://generativelanguage.googleapis.com/v1beta/models",
    headers={"x-goog-api-key": key},
    timeout=30,
)
r.raise_for_status()
for m in r.json().get("models", []):
    name = m.get("name", "").removeprefix("models/")
    methods = ", ".join(m.get("supportedGenerationMethods", []))
    print(f"{name:40}  {methods}")
