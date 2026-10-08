import os
import requests
import urllib.parse

CLIENT_ID = os.environ.get("LI_CLIENT_ID")
CLIENT_SECRET = os.environ.get("LI_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8000/callback"

if not CLIENT_ID or not CLIENT_SECRET:
    print("Error: LI_CLIENT_ID and LI_CLIENT_SECRET environment variables must be set.")
    exit(1)

def get_auth_url():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": "random_string",
        "scope": "w_member_social openid profile"
    }
    return "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode(params)

def get_token(auth_code):
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    response = requests.post("https://www.linkedin.com/oauth/v2/accessToken", data=data)
    response.raise_for_status()
    return response.json()["access_token"]

def get_profile(token):
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get("https://api.linkedin.com/v2/userinfo", headers=headers)
    response.raise_for_status()
    return response.json()["sub"]

print("Open this URL in your browser and authorize the app:")
print(get_auth_url())
print("\nAfter authorizing, you will be redirected to localhost:8000/callback?code=...")
print("Paste the FULL redirected URL here:")
url = input("URL: ")

parsed_url = urllib.parse.urlparse(url)
query_params = urllib.parse.parse_qs(parsed_url.query)
auth_code = query_params.get("code", [None])[0]

if not auth_code:
    print("Could not find the authorization code in the URL.")
    exit(1)

print("\nFetching token...")
token = get_token(auth_code)
print(f"LINKEDIN_TOKEN: {token}")

print("\nFetching user ID...")
user_id = get_profile(token)
print(f"LINKEDIN_AUTHOR: urn:li:person:{user_id}")
