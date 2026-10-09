# 🤖 AI LinkedIn Poster

An automated pipeline that picks the **most trending AI news story** from 20+ RSS feeds every morning, writes a professional LinkedIn post with an LLM, generates a matching poster image, and publishes everything to LinkedIn — all without touching a keyboard.

Runs daily on a GitHub Actions cron job. Free to operate (uses free-tier APIs).

---

## ✨ Features

- **Smart trending detection** — scores stories by recency, cross-outlet coverage, and Hacker News buzz, not just chronological order
- **Multi-model LLM fallback** — tries Gemini first, then Groq (Qwen / Llama) automatically
- **Multi-source image generation** — Gemini → Cloudflare Workers AI → Hugging Face → Pollinations → gradient fallback (always produces an image)
- **Prompt injection protection** — RSS content is isolated in `<article>` tags and sanitised before reaching the LLM
- **SSRF protection** — every redirect hop is validated against a feed-domain allowlist + private-IP block
- **Idempotent history** — if publishing fails mid-flight, the `posted.json` record is automatically rolled back
- **Dry-run mode** — on by default locally; GitHub Actions dispatch lets you preview before going live
- **32 offline unit tests** — all pass with `python -W error::ResourceWarning`

---

## 🏗️ Architecture

```
GitHub Actions cron (daily 03:30 UTC)
        │
        ▼
   trending.py          ← fetches & ranks 20+ AI RSS feeds in parallel
        │                 scores: weight × recency × coverage × HN buzz
        ▼
    post.py             ← calls LLM, validates output, publishes
    ├── gemini_helper.py   LLM: Gemini → Groq fallback
    ├── poster_helper.py   image bridge
    │       └── image_helper.py → thumb.py
    │               image: Gemini → Cloudflare → HuggingFace → Pollinations → gradient
    └── posted.json     deduplication history (committed after each live post)
```

### Module map

| File | Purpose |
|---|---|
| `post.py` | Orchestrator: fetch → write → image → publish |
| `trending.py` | RSS ingestion, ranking, deduplication, `enrich()` |
| `gemini_helper.py` | LLM caller with Gemini/Groq fallback and retry logic |
| `thumb.py` | Thumbnail generator (multi-source, guaranteed output) |
| `poster_helper.py` | Thin bridge: `make_poster(article, prompt) → path` |
| `image_helper.py` | Re-exports `make_image` from `thumb.py` |
| `get_token.py` | One-time LinkedIn OAuth2 token helper |
| `check_feeds.py` | Quick utility to verify all RSS feeds are reachable |
| `list_models.py` | Lists available Gemini models for your API key |
| `test_core.py` | Offline tests: SSRF, redirect blocking, validation, lifecycle |
| `test_pipeline.py` | End-to-end dry-run tests |

---

## 🚀 Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/ai-linkedin-poster.git
cd ai-linkedin-poster
pip install -r requirements.txt
```

### 2. Create `.env`

```ini
# LLM (at least one required)
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key          # optional fallback

# Image generation (all optional — gradient fallback always works)
CF_ACCOUNT_ID=your_cloudflare_id
CF_API_TOKEN=your_cloudflare_token
HF_TOKEN=your_huggingface_token

# LinkedIn (required for live posts)
LINKEDIN_TOKEN=your_access_token
LINKEDIN_AUTHOR=urn:li:person:XXXX

# Behaviour
DRY_RUN=1          # 1 = preview only (default), 0 = publish live
MIN_SCORE=8        # skip the day if best story scores below this
LINKEDIN_VERSION=202609
```

> **Never commit `.env`** — it is in `.gitignore`.

### 3. Get a LinkedIn token (one-time)

```bash
# Set LI_CLIENT_ID and LI_CLIENT_SECRET from your LinkedIn app
export LI_CLIENT_ID=...
export LI_CLIENT_SECRET=...
python get_token.py
```

Follow the browser prompt. The token is saved to `.linkedin_token.txt` (also gitignored). Copy `LINKEDIN_TOKEN` and `LINKEDIN_AUTHOR` into `.env` or GitHub Secrets.

Tokens expire after ~60 days — re-run `get_token.py` to refresh.

### 4. Preview locally

```bash
DRY_RUN=1 python post.py
# Writes preview.txt and image.jpg — nothing is posted
```

### 5. Publish live

```bash
DRY_RUN=0 python post.py
```

---

## ⚙️ Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — | Gemini API key (required for LLM + optional image) |
| `GROQ_API_KEY` | — | Groq API key (optional LLM fallback) |
| `GEMINI_MODELS` | `gemini-3.8-flash` | Comma-separated Gemini text model list |
| `GROQ_MODELS` | `qwen/qwen3.8-27b,llama-3.3-70b-versatile` | Groq model list |
| `GEMINI_IMAGE_MODELS` | `gemini-2.5-flash-image` | Gemini image model list |
| `CF_ACCOUNT_ID` | — | Cloudflare account ID (image generation) |
| `CF_API_TOKEN` | — | Cloudflare API token |
| `CF_IMAGE_MODEL` | `@cf/black-forest-labs/flux-1-schnell` | Cloudflare image model |
| `HF_TOKEN` | — | Hugging Face token (image generation) |
| `HF_IMAGE_MODEL` | `black-forest-labs/FLUX.1-schnell` | HF image model |
| `ENABLE_POLLINATIONS` | `1` | Set `0` to skip Pollinations (privacy) |
| `LINKEDIN_TOKEN` | — | LinkedIn OAuth2 access token |
| `LINKEDIN_AUTHOR` | — | `urn:li:person:XXXX` |
| `LINKEDIN_VERSION` | `202609` | LinkedIn API version header |
| `DRY_RUN` | `1` | `1`/`true` = preview only; `0`/`false` = publish |
| `MIN_SCORE` | `0` | Skip the day if best story is below this score |
| `NEWS_WINDOW_HOURS` | `48` | Only consider stories published in this window |
| `HN_LOOKUPS` | `15` | Number of top stories to cross-check on Hacker News |
| `IMAGE_STYLE` | *(modern illustration)* | Style suffix appended to every image prompt |
| `THUMB_TEXT` | `1` | `1` = overlay headline on thumbnail, `0` = plain image |

---

## 🔄 GitHub Actions CI/CD

The workflow (`.github/workflows/post.yml`) has two jobs:

```
test  ──► post
```

- **`test`** (5 min timeout): runs `python -m unittest discover` — the `post` job is blocked until all 32 tests pass
- **`post`** (15 min timeout): runs the poster; saves `posted.json` back to the repo on live runs

### Required GitHub Secrets

Go to **Settings → Secrets → Actions** and add:

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | From Google AI Studio |
| `GROQ_API_KEY` | From console.groq.com (optional) |
| `CF_ACCOUNT_ID` | From Cloudflare dashboard (optional) |
| `CF_API_TOKEN` | Cloudflare Workers AI token (optional) |
| `HF_TOKEN` | From huggingface.co (optional) |
| `LINKEDIN_TOKEN` | From `get_token.py` |
| `LINKEDIN_AUTHOR` | `urn:li:person:XXXX` (printed by `get_token.py`) |

### Manual dry-run dispatch

In **Actions → AI LinkedIn Poster → Run workflow**, set `dry_run: true`. The preview is uploaded as a workflow artifact.

### Schedule

Runs daily at **03:30 UTC (9:00 AM IST)**. Edit `cron` in `post.yml` to change the time.

---

## 🛡️ Security

| Control | Implementation |
|---|---|
| Secrets never printed | Only last 4 chars of token shown in `get_token.py` |
| `.env` gitignored | `.gitignore` blocks `.env`, `.linkedin_token.txt`, `scratch_*.py` |
| Prompt injection guard | RSS content wrapped in `<article>` tags, control chars stripped |
| URL/hashtag validation | Generated post is rejected if it contains a URL or > 5 hashtags |
| SSRF — scheme | Only `https://` URLs are fetched in `enrich()` |
| SSRF — allowlist | Only domains from the configured feed list are fetched |
| SSRF — private IP | `127.x`, RFC1918, loopback all blocked before any request |
| SSRF — per-hop | Every redirect hop is validated (not just the original URL) |
| SSRF — hop limit | Maximum 3 redirects before aborting |
| SSRF — size cap | Response body capped at 1.5 MB |
| LLM 429 handling | 429 breaks immediately to the next model; only 500/503 are retried |
| Publish atomicity | Record written before publish; rolled back on `HTTPError` |
| OAuth state | Random state token validated on callback |
| Token file permissions | `chmod 0600` on `.linkedin_token.txt` |
| CI permissions | `contents: read` globally; `contents: write` only on the `post` job |
| Pinned Actions SHAs | No floating `@v4` tags in the workflow |

---

## 🧪 Tests

```bash
# Run all tests
python -m unittest discover -p "test_*.py" -v

# Run with resource-leak detection
python -W error::ResourceWarning -m unittest discover -p "test_*.py"

# Compile-check all modules
python -m compileall -q .
```

**32 tests** cover:

- RSS ranking, scoring penalties (off-topic, marketing, thin)
- Link normalisation (UTM stripping, trailing slash)
- History record / unrecord / 500-entry cap
- `enrich()` SSRF: http-scheme block, unknown-domain block, private-IP block, redirect-to-foreign-domain block, 1.5 MB size cap
- LLM 429 not retried, 500/503 retried
- `write_post()` validation: short post, URL in post, too many hashtags — all trigger retry then raise
- `preflight()` 401 handling
- `publish()` → `HTTPError` removes record
- `publish()` → `ConnectionError`/`Timeout` keeps record, prints warning
- `upload_image()` → `HTTPError` removes record
- Missing / small image raises before `record_posted`

---

## 📁 Project Structure

```
ai-linkedin-poster/
├── .github/
│   └── workflows/
│       └── post.yml          # CI/CD pipeline
├── post.py                   # Main orchestrator
├── trending.py               # Feed fetching, ranking, enrich()
├── gemini_helper.py          # LLM with Gemini→Groq fallback
├── thumb.py                  # Image generation (multi-source)
├── poster_helper.py          # make_poster() bridge
├── image_helper.py           # Re-exports make_image
├── get_token.py              # LinkedIn OAuth2 token helper
├── check_feeds.py            # Verify RSS feeds are live
├── list_models.py            # List available Gemini models
├── test_core.py              # Offline security & lifecycle tests
├── test_pipeline.py          # Dry-run end-to-end tests
├── requirements.txt          # Pinned dependencies
├── .gitignore
└── posted.json               # Deduplication history (auto-updated)
```

---

## 🔧 Utilities

```bash
# Check which RSS feeds are reachable
python check_feeds.py

# List available Gemini models (text + image)
python list_models.py
```

---

## 📦 Dependencies

| Package | Version | Purpose |
|---|---|---|
| `requests` | 2.33.1 | HTTP client |
| `feedparser` | 6.0.14 | RSS/Atom parsing |
| `python-dotenv` | 1.2.2 | `.env` loading |
| `pillow` | 12.1.1 | Image processing & thumbnail overlay |

No SDKs. No paid frameworks. All APIs are called directly over HTTP.

---

## 🗺️ Roadmap

- [ ] Add more feed sources (arXiv, Papers With Code)
- [ ] Slack / Discord notification on post success
- [ ] Web UI to preview and approve before publishing
- [ ] Multi-platform support (X / Twitter, Bluesky)
- [ ] A/B test different post formats

---

## 📄 License

MIT — do whatever you want, just don't commit your secrets.
