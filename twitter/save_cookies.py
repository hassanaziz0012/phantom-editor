"""
Generates twitter/data/auth.json directly using exported auth_token and ct0 cookie values from your regular browser.

usage: 
  uv run twitter/save_cookies.py <auth_token_value> <ct0_value>
OR interactive mode:
  uv run twitter/save_cookies.py
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE_PATH = os.path.join(SCRIPT_DIR, "data", "auth.json")

def create_auth_json(auth_token: str, ct0: str):
    auth_token = auth_token.strip()
    ct0 = ct0.strip()

    data = {
        "cookies": [
            {
                "name": "auth_token",
                "value": auth_token,
                "domain": ".x.com",
                "path": "/",
                "expires": 1900000000,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax"
            },
            {
                "name": "ct0",
                "value": ct0,
                "domain": ".x.com",
                "path": "/",
                "expires": 1900000000,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax"
            }
        ],
        "origins": []
    }

    os.makedirs(os.path.dirname(AUTH_FILE_PATH), exist_ok=True)
    with open(AUTH_FILE_PATH, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\n✅ Successfully generated authentication state at:\n{AUTH_FILE_PATH}")

if __name__ == "__main__":
    if len(sys.argv) == 3:
        auth_token = sys.argv[1]
        ct0 = sys.argv[2]
    else:
        print("--- Manual Cookie Importer ---")
        auth_token = input("Enter your 'auth_token' cookie value: ").strip()
        ct0 = input("Enter your 'ct0' cookie value: ").strip()

    if not auth_token or not ct0:
        print("Error: Both auth_token and ct0 values are required.")
        sys.exit(1)

    create_auth_json(auth_token, ct0)
