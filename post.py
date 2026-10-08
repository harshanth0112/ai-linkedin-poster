import os
import sys
import json
import time
import requests
import feedparser
import urllib.parse
from datetime import datetime, timedelta, timezone
from google import genai
from google.genai import types

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
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
LINKEDIN_VERSION = os.environ.get("LINKEDIN_VERSION", "202401")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("true", "1", "yes")

# --- Helpers ---

def load_posted():
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "r") as f:
            return json.load(f)
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
                link = entry.link
                if link in posted_links:
                    continue
                
                published_parsed = entry.get('published_parsed')
                if published_parsed:
                    dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue

                articles.append({
                    "title": entry.title,
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
    client = genai.Client(api_key=GEMINI_API_KEY)
    
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
    
    delays = [5, 15, 30, 60, 60]
    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            data = json.loads(response.text)
            return data["post"], data["image_prompt"]
        except Exception as e:
            print(f"Gemini API attempt {attempt + 1} failed: {e}")
            if attempt < 4:
                time.sleep(delays[attempt])
            
    print("Failed to generate or parse content from Gemini after 5 attempts.")
    sys.exit(1)

def make_image(prompt):
    print("Generating image with Pollinations.ai...")
    safe_prompt = urllib.parse.quote(prompt + ", highly engaging YouTube thumbnail style, purely related to the topic, vibrant colors, dramatic lighting, no text")
    url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1200&height=627&nologo=true"
    
    for i in range(3):
        try:
            resp = requests.get(url)
            if resp.status_code == 200 and 'image' in resp.headers.get('content-type', ''):
                with open("image.jpg", "wb") as f:
                    f.write(resp.content)
                return "image.jpg"
            else:
                print(f"Attempt {i+1} failed: Status {resp.status_code}, Content-Type: {resp.headers.get('content-type', '')}")
        except Exception as e:
            print(f"Attempt {i+1} error: {e}")
        time.sleep(2)
    return None

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

    # 1. Initialize Upload
    init_url = "https://api.linkedin.com/rest/images?action=initializeUpload"
    init_data = {"initializeUploadRequest": {"owner": LINKEDIN_AUTHOR}}
    resp = requests.post(init_url, headers=headers, json=init_data)
    resp.raise_for_status()
    init_res = resp.json()
    upload_url = init_res["value"]["uploadUrl"]
    image_urn = init_res["value"]["image"]

    # 2. Upload Image
    with open(image_path, "rb") as f:
        image_data = f.read()
    upload_headers = {"Authorization": f"Bearer {LINKEDIN_TOKEN}"}
    put_resp = requests.put(upload_url, headers=upload_headers, data=image_data)
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
    
    post_resp = requests.post(post_url, headers=headers, json=post_data)
    post_resp.raise_for_status()
    print("Post successful!")

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
        
    image_path = make_image(image_prompt)
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
