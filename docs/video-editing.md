# Video Editing

This directory contains scripts for automating video editing tasks such as mixing background music.

## Scripts

### [add_bgm_to_video.sh](file:///home/hassan/programming/phantom-editor/video-editing/add_bgm_to_video.sh)
Mixes a background music (BGM) track into a video's audio track. Loops the music if it is shorter than the video, adjusts BGM volume, and outputs a new video file while performing a lossless video stream copy.
* **Usage**: `./video-editing/add_bgm_to_video.sh <path_to_video> <bgm_track> [--volume <percentage>]`
* **Default Output**: `<video_name>-bgm.mp4` in the same directory as the source video.
* **BGM Library**: Looks up tracks relative to `/mnt/c/Users/hassa/Videos/Asset Library/BGM` if a full path is not provided.
