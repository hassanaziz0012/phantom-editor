# Video Metadata & AI Generation

The `metadata` suite provides automatic and modular tools for creating, generating, and inspecting `metadata.json` project files for YouTube video uploads using Claude via BrowserLLM.

## CLI Commands

```bash
# Automatically create full metadata.json (timestamps, description, tweet template)
phantom meta create [<project_path>] [arguments]

# Generate human-like video description using Claude
phantom meta desc [<project_path>] [arguments]

# Generate promotional tweet template using Claude
phantom meta tweet [<project_path>] [arguments]

# Generate video chapters / timestamps using Claude
phantom meta timestamps [<captions_srt_path>] [arguments]

# Inspect parsed metadata.json
phantom meta read [<project_or_video_path>] [--json]

# Generate scored title ideas (writes titles.json + metadata.json "titles")
phantom meta titles [<project_path>] [--traffic <search|browse|both>] [--channel-size <small|mid|large>] [--thumbnail <concept>]

# List a project's generated titles, or approve one by rank or with your own text
phantom meta approve-title <project> [<rank_or_title>]

# List the resources the video promises to link (metadata.json "resources", URLs left blank for you)
phantom meta resources [<project_path>] [arguments]

# Interactively create metadata manually
phantom meta manual <video_path>
```

---

## Scripts

### [auto_create_metadata.py](../metadata/auto_create_metadata.py)
Automatically creates or updates `metadata.json` in a YouTube video project folder. When phrase-level `.srt` captions are present, it orchestrates:
1. Generating a concise, deslopified video description (`description`)
2. Generating a promotional tweet template with `{url}` placeholder (`tweetTemplate`)
3. Generating video recommendations from channel archive (`recommendations`)
4. Generating scored title ideas (`titles`, plus the full response in `titles.json`)
5. Listing the resources the video promises to link (`resources`, with blank URLs to fill in)

*Note: Timestamps/chapters are generated separately using `phantom meta timestamps` after manual video review cuts are finalized.*

`--profile` selects the BrowserLLM browser profile used for every LLM call (and for the `browserllm blank` warm-up); each individual generator below accepts it too.

* **CLI Command**: `phantom meta create [<project_path>] [--title <title>] [--tags <tags>] [--skip-ai] [--profile <name>]`

---

### [auto_gen_desc.py](../metadata/auto_gen_desc.py)
Extracts transcript cues from phrase-level `.srt` captions and sends them to Claude via BrowserLLM with anti-AI writing rules ([`deslopify_text.md`](../agentic/prompts/deslopify_text.md)) to generate a punchy 2-3 sentence video description. Saves directly to `metadata.json`.

* **CLI Command**: `phantom meta desc [<project_path>] [--metadata <path>] [--profile <name>]`

---

### [auto_gen_tweet.py](../metadata/auto_gen_tweet.py)
Analyzes video transcript cues and generates a promotional tweet template (<= 240 characters) highlighting the problem solved. Ensures the `{url}` placeholder is attached and validates total length against Twitter's limit. Saves directly to `metadata.json` as `tweetTemplate`.

* **CLI Command**: `phantom meta tweet [<project_path>] [--metadata <path>] [--profile <name>]`

---

### [generate_timestamps.py](../metadata/generate_timestamps.py)
Generates YouTube chapters and topics from phrase-level `.srt` captions using Claude via BrowserLLM. Automatically ensures the first chapter starts at `00:00` (`Introduction`) and saves the structured list to `metadata.json`.

* **CLI Command**: `phantom meta timestamps [<captions_srt_path>] [--metadata <path>] [--profile <name>]`

---

### [generate_titles.py](../metadata/generate_titles.py)
Reads `trimmed.srt` and sends the timestamped transcript to Claude via BrowserLLM with the [`generate_video_titles.md`](../agentic/prompts/generate_video_titles.md) prompt. The prompt extracts the video's key facts, writes 25 titles across 6+ archetypes, scores each out of 16, and returns the 7 best. The full response (extracted facts, all 25 titles, flags) is saved to `titles.json` in the project folder; the ranked top titles (rank, title, archetype, scores, total, payoff timestamp, thumbnail text) are saved to the `titles` list in `metadata.json`. Does not change the `title` field.

* **CLI Command**: `phantom meta titles [<project_path>] [--traffic <search|browse|both>] [--channel-size <small|mid|large>] [--thumbnail <concept>] [--metadata <path>] [--profile <name>]`

---

### [check_resource_links.py](../metadata/check_resource_links.py)
Reads `trimmed.srt` and sends the timestamped transcript to Claude via BrowserLLM with the [`check_resource_links.md`](../agentic/prompts/check_resource_links.md) prompt, which finds every resource the video promises to link ("I'll link it in the description", "linked below"). Booking-a-call mentions are deliberately ignored, since the booking link already sits at the top of every description. They're saved to the `resources` list in `metadata.json`:

```json
"resources": [
    {"key": "My n8n Beginner Tutorial", "url": "", "timestamp": "02:14", "quote": "I've got a beginner video, I'll link it in the description"}
]
```

Claude always leaves `url` blank: fill each one in yourself. `timestamp` and `quote` show where the video promises the link. Re-running keeps URLs you've already filled in (matched by `key`), along with filled-in entries Claude didn't return again. An empty list means the video links nothing.

Until every URL is filled in, `phantom projects status` doesn't count the metadata step as done, and `phantom publish yt` refuses to upload. On upload, the resources become a RESOURCES section (`key: url` per line) in the description, and the section is left out when the list is empty.

* **CLI Command**: `phantom meta resources [<project_path>] [--metadata <path>] [--profile <name>]`

---

### [create_metadata.py](../metadata/create_metadata.py)
Interactively prompts for and creates a `metadata.json` file used when uploading a video.

* **CLI Command**: `phantom meta manual <video_path>`

---

### [read_metadata.py](../metadata/read_metadata.py)
Parses, discovers, and reads `metadata.json` files for YouTube projects into typed `VideoMetadata` objects with dictionary fallback compatibility and auto-saving support.

* **CLI Command**: `phantom meta read [<project_or_video_path>] [--json]`

---

### [prepare_yt_recommendations.py](../metadata/recommendations/prepare_yt_recommendations.py)
Orchestrates YouTube channel video summarization and vector embedding generation with Google Gemini. Supports `--json` mode for machine-readable JSON summaries with suppressed logging.

* **CLI Command**: `phantom meta prepare-recommendations [<channel>] [--limit <int>] [--force] [--fresh] [--json]`

---

### [utils.py](../metadata/utils.py)
Shared helper module containing:
- `ask_browserllm(prompt_text, provider="claude")`: Invokes `browserllm -p <file> --provider claude -o <file>` (add `--output-json` via `output_json=True` for JSON prompts).
- `parse_json_response(response_text)`: Parses JSON arrays or objects from the response (handles code fences and plain text).
- `load_prompt(prompt_name, **kwargs)`: Loads prompts from `agentic/prompts/` and auto-injects deslopifier guidelines.
- `resolve_project_paths(target)`: Intelligently resolves project directory, `metadata.json`, captions `.srt`, and title.
- `parse_srt_to_timestamped_transcript(srt_path)`: Converts phrase-level `.srt` into formatted transcript lines (`[MM:SS] text`).
