---
name: generate-thumbnail
description: Use when the user asks to generate a YouTube thumbnail or any image with ChatGPT, optionally with reference images or their face.
---

# Generate a thumbnail (or image) with ChatGPT

Follow the steps IN ORDER. Do not skip steps. Do not improvise extra actions.

The user usually talks to you from the Claude mobile app, so the image MUST be sent with `SendUserFile` (Step 4). Printing the path or using Read is not enough.

## Fixed facts

- Command: `browserllm` (already installed). Always use `-P chatgpt`.
- Output folder: `/tmp/thumbnails` (create it with `mkdir -p`).
- The user's face photo: `/home/hassan/Pictures/Me/professional-photo-square.jpg`
- Projects folder: `~/Videos/YT Projects` (one sub-folder per project).

## Step 1: Collect the reference images

Make a list of images to upload, in this order:

1. EVERY image or screenshot the user attached in the chat. Assume it is a reference image, even if the user does not mention it. Use its file path. If you cannot find a file path for an attached image, tell the user and stop.
2. If the user says to use their face / "me" / "my photo" as a reference, ALSO add the face photo from Fixed facts.

Keep the order the user refers to ("reference image 1", "the second reference image"). The order of `-i` flags is the order ChatGPT sees them.

If the user's prompt refers to a reference image by number ("use the style of image 1"), keep that wording in the prompt. Do not rewrite it.

## Step 2: Write the prompt file

Use the user's prompt as written. Do not shorten it. 

Save the prompt to `/tmp/thumbnails/prompt.txt`. Using a file avoids quoting problems.

## Step 3: Run browserllm

```bash
mkdir -p /tmp/thumbnails
browserllm -P chatgpt -p /tmp/thumbnails/prompt.txt -i "REF1" -i "REF2" --image-out /tmp/thumbnails/thumb-N.png
```

- Add one `-i "PATH"` per reference image from Step 1. No reference images means no `-i` at all.
- Pick `N` as 1 for the first try, then 2, 3, ... for every retry in the same chat. Never overwrite an old file.
- This takes a few minutes. Run the command with `run_in_background: true` and wait for it to finish.
- If more than one image is created, extras are saved as `thumb-N_2.png`, `thumb-N_3.png`.
- Check the file exists with `ls /tmp/thumbnails`. If there is no image, show the user the error text and ask what to do. Do not retry in a loop (one retry max).

## Step 4: Show the image to the user

Call `SendUserFile` with:
- `files`: the absolute path(s) of the generated image(s)
- `display`: `render`
- `status`: `normal`

Then ask: **"Approve this thumbnail, or reject it (tell me what to change)?"** and wait. Do NOT attach it to any project yet.

## Step 5: Handle the answer

- **Rejected / change requests:** go back to Step 2. Use the user's new feedback as the prompt. Keep the same reference images unless told otherwise. Use the next `N` in Step 3.
- **Approved:**
  1. If the user did not say which project it is for, ask which project.
  2. Find the folder under `~/Videos/YT Projects` whose name matches. If it is not clearly one folder, list the close matches and ask.
  3. Convert and place it (this works for any image format):
     ```bash
     ffmpeg -y -i /tmp/thumbnails/thumb-N.png "$HOME/Videos/YT Projects/PROJECT NAME/thumbnail.png"
     ```
  4. Tell the user it is saved as `thumbnail.png` in that project.
- If the user only wanted an image and no project (not a thumbnail), skip the project steps. Just say where the file is.
