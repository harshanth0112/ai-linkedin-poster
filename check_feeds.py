"""Hit every RSS URL in trending.FEEDS and print HTTP status. Drop dead feeds from FEEDS."""
import requests

from trending import FEEDS, HEADERS

for url, weight in FEEDS:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        print(f"{r.status_code:>3}  w={weight}  {url}")
    except Exception as e:
        print(f"ERR  w={weight}  {url}  {type(e).__name__}")
