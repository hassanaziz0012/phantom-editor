import os
import json
import time
import requests
from dotenv import load_dotenv
load_dotenv()

# --- Configuration ---
CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8080"
LINKEDIN_API_VERSION = "202604" 

# Dynamic path to save the token file in the script's directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(SCRIPT_DIR, "linkedin_tokens.json")

def initialize_tokens():
    """
    Step 1 & 2: Handles the initial 3-legged OAuth flow using the OpenID
    and member social scopes to capture your 60-day access token.
    """
    print("--- LinkedIn API Authorization Setup ---")
    
    auth_url = (
        f"https://www.linkedin.com/oauth/v2/authorization?"
        f"response_type=code&client_id={CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&scope=openid%20profile%20w_member_social%20email"
    )
    
    print(f"\n1. Open this URL in your browser and authorize the app:\n{auth_url}\n")
    auth_code = input("2. Enter the 'code' parameter from the redirected URL: ").strip()
    
    payload = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    
    print("\nExchanging code for 60-day access token...")
    response = requests.post("https://www.linkedin.com/oauth/v2/accessToken", data=payload, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        current_time = time.time()
        
        token_data = {
            "access_token": data["access_token"],
            "access_token_expires_at": current_time + data["expires_in"]
        }
        
        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f, indent=4)
        print(f"Success! Access token securely saved to {TOKEN_FILE}.")
        return token_data
    else:
        print(f"Error initializing tokens: {response.status_code} - {response.text}")
        return None

def get_valid_access_token():
    """
    Retrieves the stored access token and runs a check against its expiration timestamp.
    """
    if not os.path.exists(TOKEN_FILE):
        print(f"Token file '{TOKEN_FILE}' not found. Initializing setup loop...")
        return initialize_tokens().get("access_token")

    with open(TOKEN_FILE, "r") as f:
        tokens = json.load(f)

    current_time = time.time()
    remaining_days = (tokens["access_token_expires_at"] - current_time) / 86400
    
    print(f"Token Check: Token is valid for another {remaining_days:.2f} days.")
    
    if remaining_days < 5:
        print("\n" + "!"*60)
        print("⚠️ WARNING: Token is nearing expiration or has expired!")
        print(f"Please delete '{TOKEN_FILE}' and run this script manually")
        print("to refresh your browser authorization link session.")
        print("!"*60 + "\n")
        
    if remaining_days <= 0:
        raise Exception("Access token has completely expired. Manual re-authentication required.")
        
    return tokens["access_token"]

def get_member_urn(access_token):
    """
    Fetches the profile info via the /v2/userinfo OpenID endpoint to identify the user URN.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0"
    }
    response = requests.get("https://api.linkedin.com/v2/userinfo", headers=headers)
    if response.status_code == 200:
        member_id = response.json().get("sub")
        return f"urn:li:person:{member_id}"
    else:
        raise Exception(f"Failed to fetch user URN: {response.status_code} - {response.text}")

if __name__ == "__main__":
    if not os.path.exists(TOKEN_FILE):
        initialize_tokens()
    else:
        print("Tokens already exist and are valid!")