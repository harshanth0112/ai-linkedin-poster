import os
import requests
import urllib.parse
import secrets
from datetime import datetime, timedelta

def main():
    CLIENT_ID = os.environ.get("LI_CLIENT_ID")
    CLIENT_SECRET = os.environ.get("LI_CLIENT_SECRET")
    REDIRECT_URI = "http://localhost:8000/callback"

    if not CLIENT_ID or not CLIENT_SECRET:
        print("Error: LI_CLIENT_ID and LI_CLIENT_SECRET environment variables must be set.")
        exit(1)

    state = secrets.token_urlsafe(16)

    def get_auth_url():
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "state": state,
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
        response = requests.post("https://www.linkedin.com/oauth/v2/accessToken", data=data, timeout=20)
        response.raise_for_status()
        return response.json()

    def get_profile(token):
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get("https://api.linkedin.com/v2/userinfo", headers=headers, timeout=20)
        response.raise_for_status()
        return response.json()["sub"]

    print("Open this URL in your browser and authorize the app:")
    print(get_auth_url())
    print("\nAfter authorizing, you will be redirected to localhost:8000/callback?code=...")
    print("Paste the FULL redirected URL here:")
    url = input("URL: ")

    parsed_url = urllib.parse.urlparse(url)
    query_params = urllib.parse.parse_qs(parsed_url.query)
    
    returned_state = query_params.get("state", [None])[0]
    if returned_state != state:
        print("Error: OAuth state mismatch.")
        exit(1)
        
    auth_code = query_params.get("code", [None])[0]

    if not auth_code:
        print("Could not find the authorization code in the URL.")
        exit(1)

    print("\nFetching token...")
    token_data = get_token(auth_code)
    token = token_data["access_token"]
    expires_in = token_data.get("expires_in", 5184000)
    
    print("\nFetching user ID...")
    user_id = get_profile(token)
    author = f"urn:li:person:{user_id}"
    
    expiry_date = datetime.now() + timedelta(seconds=expires_in)
    
    out_file = ".linkedin_token.txt"
    with open(out_file, "w") as f:
        f.write(f"LINKEDIN_TOKEN={token}\n")
        f.write(f"LINKEDIN_AUTHOR={author}\n")
        f.write(f"EXPIRY_DATE={expiry_date.isoformat()}\n")
        
    try:
        os.chmod(out_file, 0o600)
    except Exception:
        pass
        
    print(f"Token saved to {out_file} (ends in ...{token[-4:] if len(token) >= 4 else token})")
    print("Do not share this file.")

if __name__ == "__main__":
    main()
