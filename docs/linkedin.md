# LinkedIn Integration

This directory contains scripts for authenticating and posting content to LinkedIn via their API.

## Configuration
Requires `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET` environment variables.

## Scripts

### [authenticate.py](file:///home/hassan/programming/phantom-editor/linkedin/authenticate.py)
Handles the 3-legged OAuth 2.0 flow to acquire a 60-day access token and retrieve the user's member URN.
* **Usage**: `python linkedin/authenticate.py`
* **Output**: Saves credentials to `linkedin/linkedin_tokens.json`.

### [new_post.py](file:///home/hassan/programming/phantom-editor/linkedin/new_post.py)
Publishes text commentary to LinkedIn. Supports attaching multiple images or a rich link card (with custom thumbnail).
* **Usage**: `python linkedin/new_post.py` (runs demonstration post examples)
