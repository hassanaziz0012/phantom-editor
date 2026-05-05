import os
import requests
from authenticate import get_valid_access_token, get_member_urn

def upload_image_asset(access_token, author_urn, file_path):
    """Registers and uploads a local image to LinkedIn, returning its asset URN."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202604",
        "Content-Type": "application/json"
    }
    
    # Step 1: Register the image upload
    register_url = "https://api.linkedin.com/v2/images?action=initializeUpload"
    register_payload = {
        "initializeUploadRequest": {
            "owner": author_urn
        }
    }
    
    reg_response = requests.post(register_url, json=register_payload, headers=headers)
    reg_response.raise_for_status()
    
    reg_data = reg_response.json()["value"]
    upload_url = reg_data["uploadUrl"]
    image_urn = reg_data["image"]
    
    # Step 2: Upload the binary image file
    with open(file_path, "rb") as f:
        upload_headers = {"Authorization": f"Bearer {access_token}"}
        upload_response = requests.put(upload_url, data=f, headers=upload_headers)
        upload_response.raise_for_status()
        
    return image_urn

def create_post(text_content, image_paths=None, link_details=None):
    """
    Creates a LinkedIn post with text content.
    Supports either multiple images OR a rich link preview.
    
    :param text_content: Body text of the post.
    :param image_paths: List of local image file paths.
    :param link_details: Dict containing 'url', 'title', 'description', and optional local 'thumbnail_path'.
    """
    access_token = get_valid_access_token()
    author_urn = get_member_urn(access_token)
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202604",
        "Content-Type": "application/json"
    }
    
    # Construct the base post payload
    payload = {
        "author": author_urn,
        "commentary": text_content,
        "visibility": "PUBLIC",
        "lifecycleState": "PUBLISHED",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": []
        }
    }
    
    # Scenario A: Handle Rich Link Attachment
    if link_details:
        article_block = {
            "source": link_details["url"],
            "title": link_details.get("title", ""),
            "description": link_details.get("description", "")
        }
        
        # If a custom thumbnail image path is provided, upload it first
        if link_details.get("thumbnail_path"):
            print(f"Uploading link thumbnail: {link_details['thumbnail_path']}...")
            thumb_urn = upload_image_asset(access_token, author_urn, link_details["thumbnail_path"])
            article_block["thumbnail"] = thumb_urn
            
        payload["content"] = {"article": article_block}
        
    # Scenario B: Handle Multi-Image Attachment (Only if no link is provided)
    elif image_paths:
        image_urns = []
        for path in image_paths:
            print(f"Uploading {path}...")
            urn = upload_image_asset(access_token, author_urn, path)
            image_urns.append(urn)
            
        payload["content"] = {
            "multiImage": {
                "images": [{"id": urn} for urn in image_urns]
            }
        }
    
    # Publish the post
    post_url = "https://api.linkedin.com/v2/posts"
    response = requests.post(post_url, json=payload, headers=headers)
    
    if response.status_code == 201:
        print("Post created successfully!")
        return response.headers.get("x-linkedin-id")
    else:
        print(f"Failed to create post: {response.status_code} - {response.text}")
        return None

if __name__ == "__main__":
    # --- Example 1: Multi-Image Post ---
    print("--- Example 1: Multi-Image Post ---")
    text_1 = "Hello network! Check out these latest updates from our pipeline."
    images_1 = ["./linkedin/links-img.png", "./linkedin/links-img.png"]
    create_post(text_1, image_paths=images_1)
    
    # --- Example 2: Rich Link Post ---
    print("\n--- Example 2: Rich Link Post ---")
    text_2 = "I highly recommend reading our latest quarterly technical breakdown."
    rich_link = {
        "url": "https://example.com/blog/quarterly-report",
        "title": "Q1 Technical Performance & Infrastructure Milestones",
        "description": "An in-depth deep dive into design optimizations, latency metrics, and API scaling.",
        "thumbnail_path": "./linkedin/links-img.png" # Optional local image file
    }
    create_post(text_2, link_details=rich_link)