import os
from dotenv import load_dotenv
from fetch_videos import fetch_channel_videos

load_dotenv()

API_KEY = os.getenv("YOUTUBE_API_KEY")
CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")

def main():
    if not API_KEY or not CHANNEL_ID:
        print("Error: YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID environment variables are missing.")
        return

    print("Fetching videos...")
    videos = fetch_channel_videos(API_KEY, CHANNEL_ID)

    for i, v in enumerate(videos, 1):
        print(f"=== Video {i} ===")
        print(f"Title: {v.title}")
        print(f"Views: {v.view_count}")
        print(f"Likes count: {v.like_count}")
        print(f"Comments count: {v.comment_count}")
        print(f"Description:\n{v.description}")
        print("=" * 40)
        print()

if __name__ == "__main__":
    main()
