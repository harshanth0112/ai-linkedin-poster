import os
import sys
import json
import time
import requests
import feedparser
import urllib.parse
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from gemini_helper import call_gemini

# --- Configuration ---
FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/artificial-intelligence/index.xml"
]

POSTED_FILE = "posted.json"
MAX_POSTED_HISTORY = 200

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINKEDIN_TOKEN = os.environ.get("LINKEDIN_TOKEN")
LINKEDIN_AUTHOR = os.environ.get("LINKEDIN_AUTHOR")
LINKEDIN_VERSION = os.environ.get("LINKEDIN_VERSION", "202609")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("true", "1", "yes")

# --- Helpers ---

def load_posted():
    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def save_posted(posted_links):
    posted_links = posted_links[-MAX_POSTED_HISTORY:]
    with open(POSTED_FILE, "w") as f:
        json.dump(posted_links, f, indent=2)

def fetch_news(posted_links):
    print("Fetching news feeds...")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=36)
    articles = []

    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:10]:
                link = getattr(entry, 'link', None)
                if not link or link in posted_links:
                    continue
                
                published_parsed = entry.get('published_parsed')
                if published_parsed:
                    dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue

                articles.append({
                    "title": getattr(entry, 'title', 'No Title'),
                    "link": link,
                    "summary": entry.get('summary', '')[:500],
                    "published": published_parsed
                })
        except Exception as e:
            print(f"Failed to fetch {feed_url}: {e}")

    articles.sort(key=lambda x: x['published'] or time.gmtime(0), reverse=True)
    return articles

def write_post(article):
    print(f"Writing post for: {article['title']}")
    
    prompt = f"""
    You are a professional AI news curator on LinkedIn. Write a LinkedIn post based on this article:
    Title: {article['title']}
    Summary: {article['summary']}
    Link: {article['link']}

    Requirements:
    1. A strong hook to start.
    2. Summarize the key point clearly and concisely (no jargon).
    3. End with a thought-provoking question or takeaway.
    4. Exactly 3 relevant hashtags.
    5. Do NOT invent facts.
    6. Include the source link at the bottom.
    7. Length under 1200 characters.

    Also, write an image prompt that can be used to generate an accompanying AI image. 
    The image should be highly engaging, like a YouTube thumbnail, purely related to the core topic, and MUST NOT contain any text.

    Respond ONLY in valid JSON format:
    {{
        "post": "The text of the LinkedIn post",
        "image_prompt": "The prompt for the image generator"
    }}
    """
    
    text = call_gemini(prompt, json_mode=True)
    try:
        clean_text = text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
            
        data = json.loads(clean_text.strip())
        return data["post"], data["image_prompt"]
    except (json.JSONDecodeError, KeyError):
        print(f"Failed to parse Gemini response: {text[:200]}")
        sys.exit(1)

from thumb import make_image



def escape_little_text(text):
    for char in ['(', ')', '[', ']', '{', '}']:
        text = text.replace(char, f'\\{char}')
    return text

def publish_linkedin(post_text, image_path):
    print("Publishing to LinkedIn...")
    headers = {
        "Authorization": f"Bearer {LINKEDIN_TOKEN}",
        "LinkedIn-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0"
    }

    for attempt in range(3):
        try:
            # 1. Initialize Upload
            init_url = "https://api.linkedin.com/rest/images?action=initializeUpload"
            init_data = {"initializeUploadRequest": {"owner": LINKEDIN_AUTHOR}}
            resp = requests.post(init_url, headers=headers, json=init_data, timeout=30)
            resp.raise_for_status()
            init_res = resp.json()
            upload_url = init_res["value"]["uploadUrl"]
            image_urn = init_res["value"]["image"]

            # 2. Upload Image
            with open(image_path, "rb") as f:
                image_data = f.read()
            upload_headers = {"Authorization": f"Bearer {LINKEDIN_TOKEN}"}
            put_resp = requests.put(upload_url, headers=upload_headers, data=image_data, timeout=60)
            put_resp.raise_for_status()

            # 3. Create Post
            post_url = "https://api.linkedin.com/rest/posts"
            post_data = {
                "author": LINKEDIN_AUTHOR,
                "commentary": escape_little_text(post_text),
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": []
                },
                "content": {"media": {"id": image_urn}},
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False
            }
            
            post_resp = requests.post(post_url, headers=headers, json=post_data, timeout=30)
            post_resp.raise_for_status()
            print("Post successful!")
            return
        except Exception as e:
            print(f"LinkedIn publish attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(5)
            else:
                raise e

def main():
    if not (GEMINI_API_KEY and LINKEDIN_TOKEN and LINKEDIN_AUTHOR):
        print("Missing required environment variables.")
        if not DRY_RUN:
            sys.exit(1)

    posted_links = load_posted()
    articles = fetch_news(posted_links)
    
    if not articles:
        print("No new AI news found.")
        sys.exit(0)

    top_article = articles[0]
    post_text, image_prompt = write_post(top_article)
    
    with open("preview.txt", "w", encoding="utf-8") as f:
        f.write(post_text)
        
    image_path = make_image(image_prompt, headline=top_article['title'])
    if not image_path:
        print("Failed to generate image.")
        sys.exit(1)

    if DRY_RUN:
        print("DRY RUN COMPLETE. Check preview.txt and image.jpg.")
    else:
        publish_linkedin(post_text, image_path)
        posted_links.append(top_article['link'])
        save_posted(posted_links)

if __name__ == "__main__":
    main()
