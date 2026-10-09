"""
Picks the most TRENDING fresh AI story, not just the newest one.
"""
import html
import json
import math
import os
import re
import socket
from urllib.parse import urljoin, urlparse, urlunparse
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress

import feedparser
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (ai-linkedin-poster)"}
WINDOW_HOURS = int(os.getenv("NEWS_WINDOW_HOURS", "48"))
HN_LOOKUPS = int(os.getenv("HN_LOOKUPS", "15"))

FEEDS = [
# TIER 1: OFFICIAL AI COMPANIES & RESEARCH # Weight: 2.8–3.0 

("https://openai.com/news/rss.xml", 2.8), 
("https://deepmind.google/blog/rss.xml", 2.8), 
("https://blog.google/technology/ai/rss/", 2.9), 
("https://research.google/blog/rss/", 2.9), 
("https://www.microsoft.com/en-us/research/feed/", 2.8), 
("https://machinelearning.apple.com/rss.xml", 2.8), 
("https://engineering.fb.com/feed/", 2.8), 
("https://developer.nvidia.com/blog/feed/", 2.8), 
("https://huggingface.co/blog/feed.xml", 2.8), 
("https://aws.amazon.com/blogs/machine-learning/feed/", 2.8), 
("https://bair.berkeley.edu/blog/feed.xml", 2.8), 
("https://ai.stanford.edu/blog/feed.xml", 2.7), 
("https://news.mit.edu/topic/mitartificial-intelligence2-rss.xml", 2.7), 
# TIER 2: INDIA — AI, BUSINESS & GOVERNMENT # Weight: 2.5–2.8 
("https://indianexpress.com/section/technology/artificial-intelligence/feed/", 2.8), 
("https://cio.economictimes.indiatimes.com/rss/artificial-intelligence", 2.7), 
("https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=1", 2.6), 
# Google News — India-focused AI searches 
("https://news.google.com/rss/search?q=artificial+intelligence+India&hl=en-IN&gl=IN&ceid=IN:en", 2.2), 
("https://news.google.com/rss/search?q=generative+AI+India&hl=en-IN&gl=IN&ceid=IN:en", 2.2), 
("https://news.google.com/rss/search?q=AI+startups+India&hl=en-IN&gl=IN&ceid=IN:en", 2.0), 
("https://news.google.com/rss/search?q=AI+policy+India&hl=en-IN&gl=IN&ceid=IN:en", 2.0), 
# TIER 3: INDEPENDENT TECH JOURNALISM # Weight: 2.4–2.8 

("https://www.technologyreview.com/topic/artificial-intelligence/feed", 2.8), 
("https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss", 2.8), 
("https://techcrunch.com/category/artificial-intelligence/feed/", 2.7), 
("https://arstechnica.com/ai/feed/", 2.7), 
("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 2.6), 
("https://www.wired.com/feed/rss", 2.5), 
("https://venturebeat.com/feed/", 2.6), 
("https://www.theguardian.com/technology/artificialintelligenceai/rss", 2.5), 
("https://the-decoder.com/feed/", 2.5), 
("https://www.marktechpost.com/feed/", 2.2),
 # Google News — global AI coverage 
("https://news.google.com/rss/search?q=artificial+intelligence+technology&hl=en-US&gl=US&ceid=US:en", 2.1), 
("https://news.google.com/rss/search?q=AI+research+breakthrough&hl=en-US&gl=US&ceid=US:en", 2.1), 
("https://news.google.com/rss/search?q=large+language+model+release&hl=en-US&gl=US&ceid=US:en", 2.0), 
# TIER 4: AI & MACHINE LEARNING RESEARCH # Note: Research papers may be preprints. 
("https://rss.arxiv.org/rss/cs.AI", 2.7), 
("https://rss.arxiv.org/rss/cs.LG", 2.7), 
("https://rss.arxiv.org/rss/cs.CL", 2.5), 
# Research conference announcements 
("https://blog.neurips.cc/feed", 2.5), 
("https://blog.iclr.cc/feed", 2.5), 
]

def _get_domain(url):
    h = urlparse(url).hostname
    if not h: return ""
    p = h.split('.')
    return p[-2] + '.' + p[-1] if len(p) >= 2 else h

FEED_DOMAINS = {_get_domain(url) for url, _ in FEEDS}

STOP = set(
    "with from that this have will into over after about their more than what when your they been "
    "were would could also says said using used just like new".split()
)

def _strip_ctrl(text):
    if not text: return ""
    return re.sub(r'[\x00-\x1F\x7F]', '', text)

def _tokens(title):
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-\.]{2,}", title.lower()) if w not in STOP}

def _same_story(a, b):
    inter = len(a & b)
    return inter >= 3 and inter / max(1, min(len(a), len(b))) >= 0.5

_MARKETING = re.compile(
    r"(?i)(customer\s+stor(?:y|ies)|case\s+stud(?:y|ies)|"
    r"how\s+.{0,80}\b(?:cuts?|cutting|turns?|turning|saves?|saving)\b)"
)
AI_TERMS = re.compile(r"(?i)\b(ai|ml|llm|gpt|model|artificial intelligence|machine learning|deep learning|neural|openai|deepmind|anthropic)\b")

def is_marketing(title):
    return bool(_MARKETING.search(title or ""))

def normalise_link(url):
    try:
        parsed = urlparse(url)
        query = []
        for p in parsed.query.split('&'):
            if not p: continue
            k = p.split('=')[0].lower()
            if not (k.startswith('utm_') or k in ('fbclid', 'gclid', 'ref') or k.startswith('mc_')):
                query.append(p)
        path = parsed.path.rstrip('/')
        return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, '&'.join(query), ''))
    except Exception:
        return url

def _read_history(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []

def load_history(path):
    links, token_sets = set(), []
    for entry in _read_history(path):
        if isinstance(entry, str):
            links.add(normalise_link(entry))
        elif isinstance(entry, dict):
            if entry.get("link"):
                links.add(normalise_link(entry["link"]))
            if entry.get("title"):
                token_sets.append(_tokens(entry["title"]))
    return links, token_sets

def record_posted(path, article):
    history = _read_history(path)
    history.append({"link": normalise_link(article["link"]), "title": article["title"]})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history[-500:], f, indent=2, ensure_ascii=False)

def unrecord_posted(path, article):
    history = _read_history(path)
    norm = normalise_link(article["link"])
    new_hist = [h for h in history if (isinstance(h, str) and normalise_link(h) != norm) or (isinstance(h, dict) and normalise_link(h.get("link", "")) != norm)]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(new_hist[-500:], f, indent=2, ensure_ascii=False)

def _fetch_one_feed(url, weight, posted_links, seen):
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except requests.RequestException as e:
        print(f"Feed failed {url}: {type(e).__name__}")
        return []
    for e in feed.entries[:20]:
        t = e.get("published_parsed") or e.get("updated_parsed")
        link = e.get("link")
        if not t or not link: continue
        link = normalise_link(link)
        if link in posted_links or link in seen: continue
        when = datetime(*t[:6], tzinfo=timezone.utc)
        if when < cutoff: continue
        seen.add(link)
        summary = re.sub(r"<[^>]+>", " ", html.unescape(e.get("summary", "") or ""))
        items.append({
            "title": _strip_ctrl(html.unescape(e.get("title", "")).strip()),
            "link": link,
            "summary": _strip_ctrl(re.sub(r"\s+", " ", summary).strip()[:2000]),
            "when": when,
            "source": feed.feed.get("title", url),
            "weight": weight,
            "feed_url": url,
        })
    return items

def fetch_candidates(posted_links):
    items = []
    seen = set()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_fetch_one_feed, u, w, posted_links, seen) for u, w in FEEDS]
        for f in as_completed(futs):
            items.extend(f.result())
    return items

def hn_stats(link):
    try:
        r = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": link, "restrictSearchableAttributes": "url", "tags": "story", "hitsPerPage": 3},
            timeout=10,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits: return 0, 0
        best = max(hits, key=lambda h: h.get("points") or 0)
        return best.get("points") or 0, best.get("num_comments") or 0
    except requests.RequestException:
        return 0, 0

def rank(items, now=None, hn=None):
    now = now or datetime.now(timezone.utc)
    toks = [_tokens(i["title"]) for i in items]
    for idx, it in enumerate(items):
        outlets = {
            items[j]["feed_url"]
            for j in range(len(items))
            if j != idx and items[j]["feed_url"] != it["feed_url"] and _same_story(toks[idx], toks[j])
        }
        age_h = max(0.0, (now - it["when"]).total_seconds() / 3600)
        recency = 3.0 * max(0.0, 1 - age_h / WINDOW_HOURS)
        coverage = min(6.0, 2.0 * len(outlets))
        points, comments = (hn or {}).get(it["link"], (0, 0))
        buzz = min(5.0, 2 * math.log10(1 + points) + math.log10(1 + comments))
        thin = -2.0 if len(it["summary"]) < 60 else 0.0
        marketing = -2.5 if is_marketing(it["title"]) else 0.0
        offtopic = -2.0 if not AI_TERMS.search(it["title"]) and not AI_TERMS.search(it["summary"]) else 0.0
        it["covered_by"] = len(outlets)
        it["hn_points"] = points
        it["marketing"] = marketing < 0
        it["score"] = round(it["weight"] + recency + coverage + buzz + thin + marketing + offtopic, 2)
    return sorted(items, key=lambda x: x["score"], reverse=True)

def get_trending(history_path="posted.json"):
    links, hist_tokens = load_history(history_path)
    items = [
        i for i in fetch_candidates(links)
        if not any(_same_story(_tokens(i["title"]), h) for h in hist_tokens)
    ]
    if not items: return []
    first_pass = rank(items)
    
    hn = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(hn_stats, i["link"]): i["link"] for i in first_pass[:HN_LOOKUPS]}
        for f in as_completed(futs):
            hn[futs[f]] = f.result()
            
    return rank(items, hn=hn)

def enrich(article):
    """Fetch additional body text for thin articles.

    Security properties:
    - Only https:// URLs are followed (scheme checked on every hop).
    - Only URLs whose domain is in the FEED_DOMAINS allowlist are fetched
      (domain checked on every hop, including after each redirect).
    - Private / loopback IPs are rejected (IP checked on every hop).
    - Redirects are followed manually with allow_redirects=False so that
      every hop is validated before the next request is made.
    - At most MAX_HOPS (3) redirect responses are accepted.
    - Response body is capped at 1.5 MB.
    """
    if len(article.get("summary", "")) >= 500:
        return article
    try:
        MAX_HOPS = 3
        current_url = article["link"]

        for _ in range(MAX_HOPS + 1):   # up to 3 redirects → 4 iterations max
            parsed = urlparse(current_url)

            # --- per-hop scheme check ---
            if parsed.scheme != "https":
                return article

            # --- per-hop domain allowlist check ---
            domain = _get_domain(current_url)
            if domain not in FEED_DOMAINS:
                return article

            # --- per-hop private/loopback IP check ---
            try:
                ip = socket.gethostbyname(parsed.hostname)
            except socket.gaierror:
                return article
            if ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback:
                return article

            # Fetch with redirects disabled so we validate before following
            r = requests.get(
                current_url, headers=HEADERS, timeout=10,
                allow_redirects=False, stream=True,
            )

            if 300 <= r.status_code < 400:
                location = r.headers.get("Location", "").strip()
                if not location:
                    return article
                current_url = urljoin(current_url, location)  # handles relative URLs
                continue  # validate the next hop before fetching

            r.raise_for_status()

            # --- 1.5 MB size cap ---
            content = r.raw.read(1_500_000, decode_content=True)
            page = content.decode("utf-8", errors="replace")
            page = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", page)
            paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", page)
            text = " ".join(re.sub(r"<[^>]+>", " ", p) for p in paras)
            text = _strip_ctrl(re.sub(r"\s+", " ", html.unescape(text)).strip())
            if len(text) > len(article.get("summary", "")):
                return {**article, "summary": text[:2500]}
            return article

    except (requests.RequestException, socket.gaierror):
        pass
    return article
