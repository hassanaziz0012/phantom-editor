# Video Metadata & AI Generation

The `metadata` suite provides automatic and modular tools for creating, generating, and inspecting `metadata.json` project files for YouTube video uploads using Claude via BrowserLLM.

## CLI Commands

```bash
# Automatically create full metadata.json (timestamps, description, tweet template)
phantom metadata create [<project_path>] [arguments]

# Generate human-like video description using Claude
phantom metadata desc [<project_path>] [arguments]

# Generate promotional tweet template using Claude
phantom metadata tweet [<project_path>] [arguments]

# Generate video chapters / timestamps using Claude
phantom metadata timestamps [<captions_srt_path>] [arguments]

# Inspect parsed metadata.json
phantom metadata read [<project_or_video_path>] [--json]

# Interactively create metadata manually
phantom metadata manual <video_path>
```

---

## Scripts

### [auto_create_metadata.py](../metadata/auto_create_metadata.py)
Automatically creates or updates `metadata.json` in a YouTube video project folder. When phrase-level `.srt` captions are present, it orchestrates:
1. Generating video topics and chapters (`timestamps`)
2. Generating a concise, deslopified video description (`description`)
3. Generating a promotional tweet template with `{url}` placeholder (`tweetTemplate`)

* **CLI Command**: `phantom metadata create [<project_path>] [--title <title>] [--tags <tags>] [--skip-ai]`

---

### [auto_gen_desc.py](../metadata/auto_gen_desc.py)
Extracts transcript cues from phrase-level `.srt` captions and sends them to Claude via BrowserLLM with anti-AI writing rules ([`deslopify_text.md`](../agentic/prompts/deslopify_text.md)) to generate a punchy 2-3 sentence video description. Saves directly to `metadata.json`.

* **CLI Command**: `phantom metadata desc [<project_path>] [--metadata <path>]`

---

### [auto_gen_tweet.py](../metadata/auto_gen_tweet.py)
Analyzes video transcript cues and generates a promotional tweet template (<= 240 characters) highlighting the problem solved. Ensures the `{url}` placeholder is attached and validates total length against Twitter's limit. Saves directly to `metadata.json` as `tweetTemplate`.

* **CLI Command**: `phantom metadata tweet [<project_path>] [--metadata <path>]`

---

### [generate_timestamps.py](../metadata/generate_timestamps.py)
Generates YouTube chapters and topics from phrase-level `.srt` captions using Claude via BrowserLLM. Automatically ensures the first chapter starts at `00:00` (`Introduction`) and saves the structured list to `metadata.json`.

* **CLI Command**: `phantom metadata timestamps [<captions_srt_path>] [--metadata <path>]`

---

### [create_metadata.py](../metadata/create_metadata.py)
Interactively prompts for and creates a `metadata.json` file used when uploading a video.

* **CLI Command**: `phantom metadata manual <video_path>` or `python metadata/create_metadata.py <video_path>`

---

### [read_metadata.py](../metadata/read_metadata.py)
Parses, discovers, and reads `metadata.json` files for YouTube projects into typed `VideoMetadata` objects with dictionary fallback compatibility and auto-saving support.

* **CLI Command**: `phantom metadata read [<project_or_video_path>] [--json]` or `python metadata/read_metadata.py [<project_or_video_path>] [--json]`

---

### [utils.py](../metadata/utils.py)
Shared helper module containing:
- `query_claude(prompt_text)`: Invokes `browserllm -p <file> --provider claude -o <file>`.
- `parse_claude_json(response_text)`: Parses JSON arrays or objects from Claude's response (handles code fences and plain text).
- `load_prompt(prompt_name, **kwargs)`: Loads prompts from `agentic/prompts/` and auto-injects deslopifier guidelines.
- `resolve_project_paths(target)`: Intelligently resolves project directory, `metadata.json`, captions `.srt`, and title.
- `parse_srt_to_timestamped_transcript(srt_path)`: Converts phrase-level `.srt` into formatted transcript lines (`[MM:SS] text`).
