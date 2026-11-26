# Twitter (X) Integration

This directory contains automation scripts to post tweets and read the X feed using Playwright. They use the shared Chrome profile (see [browser/cdp.py](../browser/cdp.py)), so you stay logged in to X there; there is no separate login script.

## Scripts

### [post_tweet.py](../twitter/post_tweet.py)
Automates tweeting by attaching Playwright to the shared Chrome profile (see [browser/cdp.py](../browser/cdp.py)). Supports attaching an image from the clipboard (`--image`, read with `wl-paste`/`xclip`) or from a file (`--image-file <path>`) and cross-posting to Substack Notes.

* **Usage**: `phantom publish x [--image | --image-file <path>] [--substack] "tweet content"`

### [feed.py](../twitter/feed.py)
Reads your X home feed for `phantom ideas from-x`. It attaches to the logged-in Chrome on port 9222 (the same shared Chrome profile), scrolls `x.com/home`, and reads the `HomeTimeline` GraphQL responses for exact like, repost, quote, reply, bookmark and view counts. Ads are skipped and reposts count as the original post. Posts are ranked by `log10(1 + engagement per hour) + 0.5 × log10(1 + 100 × engagement per follower)`, which favours posts taking off fast and posts doing far better than their author usually does.

## Video ideas from the feed

`phantom ideas from-x [--no-export] [--profile p9223]` ([ideas/from_x.py](../ideas/from_x.py)) reads about 250 feed posts, sends the 90 fastest-rising from the last 72 hours to Claude via BrowserLLM with your best home-feed videos and latest uploads from Studio, and gets back 3–5 ideas. The prompt is [generate_ideas_from_x_feed.md](../agentic/prompts/generate_ideas_from_x_feed.md). Each idea is the story, when to film, one take, one title and the key post. They're printed, saved to `data/x/<timestamp>.md` and added to the Ideas sheet with Source Type "X Feed".
