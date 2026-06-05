"""
YouTube Video Comment Sentiment Analyzer
========================================
Fetches a video's metadata and comments using the YouTube Data API v3,
then formats and sends them to Gemini-3.5-flash via LangChain to run sentiment analysis.
"""

import os
import sys
import re
import argparse
from pathlib import Path
from typing import Optional, List, Dict

from dotenv import load_dotenv

# Add the project root directory to sys.path to allow absolute package imports
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

SYSTEM_PROMPT = """You are an expert data analyst. Analyze the sentiment of the following YouTube video comments.

For each comment:
1. Extract the comment ID.
2. Extract the author's username.
3. Extract the comment text.
4. Determine the sentiment: Positive, Negative, Neutral, or Mixed. 
5. Extract the primary reason for that sentiment.
6. Provide a confidence score (0.0 to 1.0).

In addition to individual analyses, provide a brief summary of the overall sentiment and main themes in the batch of comments.

IMPORTANT: To facilitate spreadsheet organization and categorization, you MUST reuse the exact same reason strings/phrases across comments where possible. Avoid slightly different wordings for identical reasons (for example, reuse standard reasons like "Appreciates video editing", "Audio volume too low", "Thanks creator", "Asks a question", "Technical issue", "Request for tutorial" rather than writing new unique descriptions for each comment).

Expected Schema Structure:
{{
  "analyses": [
    {{
      "comment_id": "string",
      "username": "string",
      "text": "string",
      "sentiment": "Positive" | "Negative" | "Neutral" | "Mixed",
      "reason": "string",
      "confidence_score": float
    }}
  ],
  "summary": "string"
}}
"""


# Load environment variables
load_dotenv()


def extract_video_id(url_or_id: str) -> str:
    """
    Extract the 11-character video ID from a YouTube URL or return it if it's already a valid ID.
    """
    url_or_id = url_or_id.strip()
    
    # Check if the input is directly a video ID (usually 11 characters, alphanumeric + underscore + hyphen)
    if len(url_or_id) == 11 and re.match(r"^[a-zA-Z0-9_-]{11}$", url_or_id):
        return url_or_id
    
    # Regexes for various youtube URL structures
    patterns = [
        r"(?:v=|\/v\/|embed\/|shorts\/|youtu\.be\/|\/watch\?v=)([a-zA-Z0-9_-]{11})",
        r"(?:watch\?.*v=)([a-zA-Z0-9_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
            
    raise ValueError(f"Could not extract a valid 11-character YouTube video ID from: {url_or_id}")


def fetch_video_metadata(youtube, video_id: str) -> Dict[str, str]:
    """
    Fetch the title, description, and view count of a YouTube video.
    """
    print(f"[Status] Fetching metadata for video ID: {video_id}...")
    try:
        response = youtube.videos().list(
            part="snippet,statistics",
            id=video_id
        ).execute()
    except Exception as e:
        print(f"[Error] Failed to communicate with YouTube API: {e}", file=sys.stderr)
        sys.exit(1)
        
    items = response.get("items", [])
    if not items:
        print(f"[Error] Video with ID {video_id} not found.", file=sys.stderr)
        sys.exit(1)
        
    snippet = items[0]["snippet"]
    statistics = items[0]["statistics"]
    
    return {
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "view_count": statistics.get("viewCount", "0")
    }


def fetch_comments(youtube, video_id: str, limit: Optional[int] = None) -> List[Dict[str, str]]:
    """
    Fetch comments from the video. If limit is provided, fetch at most that many comments.
    """
    if limit is not None:
        print(f"[Status] Fetching comments for video ID: {video_id} (limit: {limit})...")
    else:
        print(f"[Status] Fetching comments for video ID: {video_id} (no limit)...")
        
    comments = []
    next_page_token = None
    
    try:
        while True:
            # Determine maxResults to request (capped at 100 by the YouTube API)
            max_results = 100
            if limit is not None:
                remaining = limit - len(comments)
                if remaining <= 0:
                    break
                max_results = min(100, remaining)
                
            response = youtube.commentThreads().list(
                part="snippet",
                videoId=video_id,
                maxResults=max_results,
                pageToken=next_page_token,
                textFormat="plainText"
            ).execute()
            
            items = response.get("items", [])
            for item in items:
                snippet = item["snippet"]["topLevelComment"]["snippet"]
                author = snippet.get("authorDisplayName", "Unknown")
                text = snippet.get("textDisplay", "")
                comments.append({"author": author, "text": text})
                
                if limit is not None and len(comments) >= limit:
                    break
            
            print(f"      Fetched {len(comments)} comments so far...")
            
            if limit is not None and len(comments) >= limit:
                break
                
            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break
    except Exception as e:
        err_msg = str(e)
        if "commentsDisabled" in err_msg or "disabled" in err_msg.lower():
            print("[Status] Comments are disabled for this video.")
        else:
            print(f"[Warning] Error occurred while fetching comments: {e}. Continuing with comments gathered so far.")
            
    return comments


def main():
    parser = argparse.ArgumentParser(description="Analyze YouTube video comment sentiment using LangChain and Gemini.")
    parser.add_argument(
        "video",
        nargs="?",
        default=None,
        help="YouTube video URL or 11-character video ID."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of comments to fetch and analyze. (Fetches all by default)"
    )
    parser.add_argument(
        "--model",
        choices=["gemini-3.5-flash", "gemma-4"],
        default="gemini-3.5-flash",
        help="The LLM model to use for analysis. (gemini-3.5-flash or gemma-4)"
    )
    args = parser.parse_args()
    
    # Load dependencies for LangChain, Gemini, and Pandas after parsing arguments to ensure quick --help response
    from youtube_api.utils import get_youtube_client
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.prompts import ChatPromptTemplate
        from pydantic import BaseModel, Field
        from typing import List, Literal
        import json
        import pandas as pd
    except ImportError as e:
        print(f"[Error] Missing dependency. Please run 'uv add langchain langchain-google-genai pandas' to install. Details: {e}", file=sys.stderr)
        sys.exit(1)
    
    # 1. Verify Credentials
    youtube_api_key = os.getenv("YOUTUBE_API_KEY")
    if not youtube_api_key:
        print("[Error] YOUTUBE_API_KEY is not set in .env", file=sys.stderr)
        sys.exit(1)
        
    gemini_api_key = os.getenv("GEMINI_API_KEY_1")
    if not gemini_api_key:
        print("[Error] GEMINI_API_KEY_1 is not set in .env", file=sys.stderr)
        sys.exit(1)
        
    # 2. Get Input Video ID/URL
    target_video = args.video
    if not target_video:
        target_video = input("Please enter the YouTube video URL or ID: ").strip()
        if not target_video:
            print("[Error] No video URL or ID provided.", file=sys.stderr)
            sys.exit(1)
            
    try:
        video_id = extract_video_id(target_video)
    except ValueError as e:
        print(f"[Error] {e}", file=sys.stderr)
        sys.exit(1)
        
    # 3. Fetch Video Details & Comments
    print("[Status] Initializing YouTube API client...")
    youtube_client = get_youtube_client(youtube_api_key)
    
    metadata = fetch_video_metadata(youtube_client, video_id)
    print(f"      Title: {metadata['title']}")
    print(f"      Views: {int(metadata['view_count']):,}" if metadata['view_count'].isdigit() else f"      Views: {metadata['view_count']}")
    
    comments = fetch_comments(youtube_client, video_id, limit=args.limit)
    print(f"[Status] Successfully retrieved {len(comments)} comments.")
    
    # Assign sequential comment IDs (c1, c2, ...)
    for i, c in enumerate(comments):
        c["comment_id"] = f"c{i+1}"
    
    # 4. Setup LLM Bot using LangChain
    model_name = args.model
    api_model = "gemma-4-31b-it" if model_name == "gemma-4" else model_name
    print(f"[Status] Initializing ChatGoogleGenerativeAI model ({model_name})...")
    try:
        llm = ChatGoogleGenerativeAI(
            model=api_model,
            google_api_key=gemini_api_key,
            temperature=0.0
        )
    except Exception as e:
        print(f"[Error] Failed to initialize model client: {e}", file=sys.stderr)
        sys.exit(1)
        
    # Define the output schema using Pydantic
    class CommentSentiment(BaseModel):
        comment_id: str = Field(description="The ID of the comment being analyzed.")
        username: str = Field(description="The username of the comment author.")
        text: str = Field(description="The text of the comment.")
        sentiment: Literal["Positive", "Negative", "Neutral", "Mixed"] = Field(description="The overall sentiment of the comment.")
        reason: str = Field(description="The primary reason for the sentiment.")
        confidence_score: float = Field(description="Confidence score between 0.0 and 1.0.")

    class SentimentResponse(BaseModel):
        analyses: List[CommentSentiment] = Field(description="List of comment sentiment analyses.")
        summary: str = Field(description="A summary of users' sentiments for the comments in this batch.")

    # Configure the LLM to return structured output matching the schema
    try:
        structured_llm = llm.with_structured_output(SentimentResponse)
    except Exception as e:
        print(f"[Error] Failed to configure structured output: {e}", file=sys.stderr)
        sys.exit(1)
        
    # Use the SYSTEM_PROMPT defined at the top of the file
    system_prompt = SYSTEM_PROMPT
    
    # User prompt template contains video details and the formatted comments
    user_prompt = """Analyze the sentiment of comments in this YouTube video. 
Video Title: {title}
Description: {description}
View Count: {view_count}

Comments:
{comments}"""

    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", user_prompt)
    ])
    
    # Assemble Chain
    chain = prompt_template | structured_llm
    
    # 5. Invoke LLM Chain in chunks of 50 comments max
    aggregated_results = []
    chunk_summaries = []
    chunk_size = 50
    
    if not comments:
        print("[Status] No comments to analyze.")
    else:
        print(f"[Status] Analyzing {len(comments)} comments in chunks of {chunk_size}...")
        for start_idx in range(0, len(comments), chunk_size):
            chunk = comments[start_idx:start_idx + chunk_size]
            print(f"      Processing chunk {start_idx // chunk_size + 1} (comments {start_idx + 1} to {min(start_idx + chunk_size, len(comments))})...")
            
            formatted_comments = "\n".join([f"{c['comment_id']}: {c['author']}: {c['text']}" for c in chunk])
            
            try:
                response = chain.invoke({
                    "title": metadata["title"],
                    "description": metadata["description"],
                    "view_count": metadata["view_count"],
                    "comments": formatted_comments
                })
                
                # Extract and append structured results
                for analysis in response.analyses:
                    aggregated_results.append({
                        "comment_id": analysis.comment_id,
                        "username": analysis.username,
                        "text": analysis.text,
                        "sentiment": analysis.sentiment,
                        "reason": analysis.reason,
                        "confidence_score": analysis.confidence_score
                    })
                
                if hasattr(response, "summary") and response.summary:
                    chunk_summaries.append(response.summary)
            except Exception as e:
                print(f"[Error] Failed to analyze chunk starting at index {start_idx}: {e}", file=sys.stderr)
                sys.exit(1)
                
    print(f"[Status] Received response from {model_name}. Printing sentiment analysis:")
    print("=" * 60)
    print(json.dumps(aggregated_results, indent=2))
    print("=" * 60)
    
    if chunk_summaries:
        print("\n[Status] Sentiment Summary:")
        print("-" * 60)
        for i, summary in enumerate(chunk_summaries):
            if len(chunk_summaries) > 1:
                print(f"Batch {i+1} Summary: {summary}")
            else:
                print(summary)
        print("-" * 60)
        
    if aggregated_results:
        # 6. Create a Pandas dataframe of the entire analysis
        print("[Status] Creating Pandas DataFrame...")
        df = pd.DataFrame(aggregated_results)
        # Ensure correct column ordering: comment_id, username, text, sentiment, reason, confidence_score
        df = df[["comment_id", "username", "text", "sentiment", "reason", "confidence_score"]]
        
        # Print the rows with the most common reasons
        if not df.empty:
            reason_counts = df['reason'].value_counts()
            if not reason_counts.empty:
                max_count = reason_counts.max()
                most_common_reasons = reason_counts[reason_counts == max_count].index.tolist()
                
                print(f"\n[Status] Rows with the most common reason(s) (Frequency: {max_count}):")
                print("-" * 80)
                common_reasons_df = df[df['reason'].isin(most_common_reasons)]
                
                # Configure pandas print options to display columns cleanly
                pd.set_option('display.max_columns', None)
                pd.set_option('display.max_colwidth', None)
                pd.set_option('display.width', 1000)
                
                print(common_reasons_df.to_string(index=False))
                print("-" * 80)
        
        # 7. Save this dataframe as a CSV file: "sentiment_{video_title_in_underscores}.csv"
        # TODO(security): Sanitize the video title to prevent path traversal (CWE-22)
        video_title = metadata.get("title", "video")
        video_title_slug = re.sub(r'[^a-zA-Z0-9]+', '_', video_title).strip('_')
        if not video_title_slug:
            video_title_slug = video_id
        csv_filename = f"sentiment_{video_title_slug}.csv"
        
        print(f"[Status] Saving analysis to CSV file: {csv_filename}...")
        try:
            df.to_csv(csv_filename, index=False)
            print(f"[Status] Successfully saved {len(df)} rows to {csv_filename}")
        except Exception as e:
            print(f"[Error] Failed to save CSV file: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
