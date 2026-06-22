import re
from pathlib import Path

# Google API client library imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/documents.readonly"]

def format_srt_time(seconds: float) -> str:
    """Converts seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int(round((seconds % 1) * 1000))
    if milliseconds == 1000:
        milliseconds = 0
        secs += 1
        if secs == 60:
            secs = 0
            minutes += 1
            if minutes == 60:
                minutes = 0
                hours += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def parse_timestamp(val: str) -> float:
    """Parses float seconds or HH:MM:SS,mmm formatted timestamps into float seconds."""
    val = val.strip()
    if not val:
        return 0.0
    try:
        return float(val)
    except ValueError:
        pass
    
    val = val.replace(',', '.')
    parts = val.split(':')
    if len(parts) == 3:
        # HH:MM:SS.mmm
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2])
        return h * 3600 + m * 60 + s
    elif len(parts) == 2:
        # MM:SS.mmm
        m = int(parts[0])
        s = float(parts[1])
        return m * 60 + s
    else:
        raise ValueError(f"Invalid timestamp format: '{val}'. Expected float seconds or HH:MM:SS,mmm")


def slugify(text: str) -> str:
    """Safely converts text to a lowercase hyphenated slug filename (alphanumeric and hyphens only)."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text)
    return text.strip('-')


GDOCS_TOKEN_FILE = Path(__file__).resolve().parent / "tokens/gdocs_token.json"


def find_client_secrets():
    """Locate client_secret.json across common directories."""
    # 1. video-editing/tokens/client_secret.json
    p1 = Path(__file__).resolve().parent / "tokens/client_secret.json"
    if p1.exists():
        return p1
    # 2. youtube_api/tokens/client_secret.json
    p2 = Path(__file__).resolve().parent.parent / "youtube_api/tokens/client_secret.json"
    if p2.exists():
        return p2
    # 3. root tokens/client_secret.json
    p3 = Path(__file__).resolve().parent.parent / "tokens/client_secret.json"
    if p3.exists():
        return p3
    return p1  # Default fallback path


def get_google_doc_shorts(doc_id: str, credentials_file: Path, token_file: Path):
    """Fetches the Google Doc content and parses it into {title, body} shorts."""
    if not doc_id:
        raise ValueError("Google Doc ID is missing. Set SHORTS_GOOGLE_DOC_ID in your environment or .env file.")

    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception as e:
            print(f"⚠️  Error reading token file {token_file}: {e}. Re-authenticating...")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"⚠️  Failed to refresh credentials: {e}. Re-running auth flow...")
                creds = None
        
        if not creds:
            if not credentials_file.exists():
                raise FileNotFoundError(
                    f"Google client_secret.json credentials file not found.\n"
                    f"Searched paths:\n"
                    f" - {Path(__file__).resolve().parent / 'tokens/client_secret.json'}\n"
                    f" - {Path(__file__).resolve().parent.parent / 'youtube_api/tokens/client_secret.json'}\n"
                    f"Please verify client_secret.json exists."
                )
            
            # Ensure tokens parent folder exists
            token_file.parent.mkdir(parents=True, exist_ok=True)
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open(token_file, "w") as f:
            f.write(creds.to_json())

    service = build("docs", "v1", credentials=creds)
    print(f"📖 Fetching Google Doc ID: {doc_id}...")
    doc = service.documents().get(documentId=doc_id).execute()
    
    shorts = []
    current_short = None
    
    body_elements = doc.get("body", {}).get("content", [])
    for elem in body_elements:
        if "paragraph" in elem:
            para = elem["paragraph"]
            style = para.get("paragraphStyle", {}).get("namedStyleType", "")
            
            # Extract plain text content
            text = ""
            for part in para.get("elements", []):
                if "textRun" in part:
                    text += part["textRun"].get("content", "")
            
            text_str = text.strip()
            if not text_str:
                continue
                
            if style == "HEADING_2":
                if current_short:
                    shorts.append(current_short)
                current_short = {
                    "title": text_str,
                    "body": ""
                }
            elif style == "NORMAL_TEXT":
                if current_short:
                    if current_short["body"]:
                        current_short["body"] += "\n" + text_str
                    else:
                        current_short["body"] = text_str
                        
    if current_short:
        shorts.append(current_short)
        
    return shorts
