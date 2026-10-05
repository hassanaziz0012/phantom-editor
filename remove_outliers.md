# Implementation Plan: Remove Outliers Feature

This implementation plan outlines the exact, step-by-step process to safely remove the entire **outliers** featureset (PostgreSQL database, Docker container, outlier scoring engine, and channel history scraper) while preserving **`phantom ideas generate-from-outlier`**, **`phantom ideas generate-from-comments`**, and **`phantom yt summarize`** as standalone, decoupled tools.

---

## 1. Overview & Scope

### What Will Be Removed
1. **`ideas/outliers_db/`** (entire directory containing 13 files):
   * `__init__.py`, `__main__.py`, `cli.py`
   * `channels.py`, `videos.py`, `models.py`, `queries.py`
   * `config.py`, `connection.py`, `docker.py`, `schema.py`, `scoring.py`
2. **`ideas/scrape_videos.py`**:
   * Scrapes 12-month channel video histories, calculates outlier scores, and saves to PostgreSQL.
3. **`ideas/tech_channels.txt`**:
   * Sample channel list for `scrape_videos.py`.
4. **PostgreSQL Docker Resources**:
   * Docker container `phantom-postgres` and volume `phantom_postgres_data`.
5. **Python Dependency**:
   * `psycopg[binary]>=3.2.0` in `pyproject.toml`.
6. **CLI Commands in `./phantom`**:
   * `phantom ideas outliers`
   * `phantom ideas scrape` / `scrape-videos`

### What Will Be Preserved (and Decoupled)
1. **`phantom ideas generate-from-outlier <video_url>`** (`ideas/generate_from_outlier.py`):
   * Continues to generate creative angles and content ideas from a specific video URL/ID.
   * Decoupled to fetch metadata via YouTube Data API / `yt-dlp` and summarize on-the-fly via OpenRouter/Groq Whisper without requiring PostgreSQL or Docker.
2. **`phantom ideas generate-from-comments <video_url>`** (`ideas/comments/generate_ideas.py`):
   * Fully independent; remains untouched.
3. **`phantom ideas export`** (`ideas/export_ideas_to_sheet.py`):
   * Remains untouched.
4. **`phantom yt summarize <video>`** (`ideas/summarize_video.py`):
   * Decoupled to summarize a single video without attempting to write to PostgreSQL.

---

## 2. Step-by-Step Implementation

### Step 1: Decouple `ideas/generate_from_outlier.py`

File: `ideas/generate_from_outlier.py`

#### Changes:
1. **Remove DB Lookup Function**
   Delete `find_video_in_db` (lines 101–142):
   ```python
   # REMOVE THIS ENTIRE FUNCTION:
   def find_video_in_db(video_id: str) -> Optional[Dict[str, Any]]: ...
   ```

2. **Update `get_or_create_summary`** (around lines 198–232):
   Remove the `db_record` parameter and DB cache reading. Change the call to `summarize_video` so it does not pass `save_to_db=True`:
   ```python
   def get_or_create_summary(
       video_id: str,
       model: Optional[str] = None,
   ) -> Tuple[str, List[str]]:
       """
       Executes ideas/summarize_video.py to download, transcribe, and summarize
       the video via OpenRouter and Groq Whisper.

       Returns:
           (summary_text, takeaways_list)
       """
       from ideas.summarize_video import DEFAULT_MODEL, summarize_video

       summary_model = model or DEFAULT_MODEL
       logger.info("Summarizing video %s via OpenRouter (%s)...", video_id, summary_model)
       summary_obj, _ = summarize_video(video_id, model=summary_model)

       return summary_obj.summary, summary_obj.takeaways
   ```

3. **Update `main()` Execution Flow** (around lines 536–585):
   Replace the database lookup block with direct metadata fetching:
   ```python
       # 2. Fetch Video Metadata
       if not args.json:
           print(f"{Colors.CYAN}Fetching metadata for video {Colors.BOLD}{video_id}{Colors.RESET}...{Colors.RESET}")

       meta = fetch_video_metadata(video_id)
       title = meta.get("title", "")
       channel_name = meta.get("channel_title", "")
       description = meta.get("description", "")
       view_count = meta.get("view_count")
       outlier_score = None
       is_outlier = False

       if not args.json:
           print(f"  {Colors.GREEN}✓ Title: {title}{Colors.RESET}")
           print(f"  {Colors.GREEN}✓ Channel: {channel_name}{Colors.RESET}")

       # 3. Summarize the Video
       if not args.json:
           print(f"{Colors.CYAN}Summarizing video audio and generating takeaways via OpenRouter...{Colors.RESET}")

       try:
           summary, takeaways = get_or_create_summary(
               video_id=video_id,
               model=args.model,
           )
       except Exception as e:
           if args.json:
               print(json.dumps({"error": f"Summarization failed: {e}"}, indent=2))
           else:
               print(f"{Colors.RED}Error summarizing video:{Colors.RESET} {e}", file=sys.stderr)
           sys.exit(1)
   ```

4. **Update CLI argument parser in `main()`**:
   * Update the docstring / description: remove mentions of PostgreSQL.
   * `--fresh-summary`: You can retain the flag with `help="Force a fresh video summary (retained for backward compatibility)."` or remove it.

---

### Step 2: Decouple `ideas/summarize_video.py`

File: `ideas/summarize_video.py`

#### Changes:
1. **Remove DB Imports** (lines 39–40):
   ```python
   # REMOVE THESE LINES:
   from ideas.outliers_db.schema import init_db
   from ideas.outliers_db.videos import get_unsummarized_videos, update_video_summary, get_video
   ```

2. **Update `summarize_video()`** (around lines 262–322):
   * Remove `save_to_db: bool = True` argument (or default to `False` and make it a no-op).
   * Remove the DB persistence block:
   ```python
   # REMOVE THIS BLOCK:
   if save_to_db:
       try:
           init_db()
           update_video_summary(...)
   ```

3. **Remove DB Bulk Summarization**:
   * Delete or deprecate `summarize_bulk_videos` (lines 324–410) as it exclusively queries PostgreSQL `get_unsummarized_videos()`.

4. **Update `main()` CLI arguments & dispatch** (around lines 412–516):
   * Remove `--all`, `--limit`, `--workers`, and `--no-db` arguments.
   * Make `video` a positional required argument:
   ```python
   parser.add_argument(
       "video",
       help="YouTube video URL or 11-character Video ID (e.g. https://www.youtube.com/watch?v=... or dQw4w9WgXcQ).",
   )
   ```
   * Remove bulk branch: directly execute single video summarization (`summary_obj, v_id = summarize_video(video_input=args.video, model=args.model)`).

---

### Step 3: Remove Outlier Files & Folders

Delete the following directories and files:
```bash
rm -rf ideas/outliers_db
rm -f ideas/scrape_videos.py
rm -f ideas/tech_channels.txt
```

---

### Step 4: Update CLI Dispatcher (`phantom`)

File: `phantom`

#### 1. Update `ideas` subcommand dispatcher (around lines 314–343):
Change:
```bash
    ideas)
        SUBCOMMAND="$1"
        shift
        if [ "$SUBCOMMAND" = "generate-from-comments" ] || [ "$SUBCOMMAND" = "from-comments" ]; then
            if [ $# -eq 0 ]; then
                echo "Usage: phantom ideas generate-from-comments <video> [arguments]"
                exit 1
            fi
            uv run --project "$BASE_DIR" python "$BASE_DIR/ideas/comments/generate_ideas.py" "$@"
        elif [ "$SUBCOMMAND" = "generate-from-outlier" ] || [ "$SUBCOMMAND" = "from-outlier" ]; then
            if [ $# -eq 0 ]; then
                echo "Usage: phantom ideas generate-from-outlier <video> [arguments]"
                exit 1
            fi
            uv run --project "$BASE_DIR" python "$BASE_DIR/ideas/generate_from_outlier.py" "$@"
        elif [ "$SUBCOMMAND" = "export" ]; then
            uv run --project "$BASE_DIR" python "$BASE_DIR/ideas/export_ideas_to_sheet.py" "$@"
        else
            echo "Unknown ideas subcommand: $SUBCOMMAND"
            echo "Usage: phantom ideas generate-from-comments <video> [arguments]"
            echo "       phantom ideas generate-from-outlier <video> [arguments]"
            echo "       phantom ideas export [--file <path> | --idea <text>] [arguments]"
            exit 1
        fi
        ;;
```

#### 2. Update usage messages in `phantom`:
* Under `show_usage()` (around line 63):
  Remove:
  ```bash
      echo "    scrape (<channel_or_file> | --channels <path>) [--months <int>] [--threshold <float>] [--json] [arguments]"
      echo "    outliers [arguments]"
  ```
  Update `yt summarize`:
  ```bash
      echo "    summarize <video> [--model <model>] [--json]"
  ```

---

### Step 5: Clean Up Dependencies (`pyproject.toml`)

File: `pyproject.toml`

1. Remove `"psycopg[binary]>=3.2.0",` from `dependencies` list.
2. Synchronize virtual environment lock:
   ```bash
   uv lock
   ```

---

### Step 6: Stop & Clean Up Docker Resources

Run the following commands in the shell to clean up PostgreSQL container and volume:
```bash
docker stop phantom-postgres
docker rm phantom-postgres
docker volume rm phantom_postgres_data
```

---

## 3. Verification & Testing

Execute the following commands to confirm everything functions without error:

1. **Verify No Lingering References**:
   ```bash
   git grep -i "outliers_db"
   ```
   *Expected result: 0 matching lines.*

2. **Verify CLI Help Menus**:
   ```bash
   ./phantom ideas --help
   ./phantom yt --help
   ```
   *Ensure `phantom ideas` lists only `generate-from-comments`, `generate-from-outlier`, and `export`.*

3. **Verify Python Import Integrity**:
   ```bash
   uv run python -c "import ideas.generate_from_outlier; print('generate_from_outlier: OK')"
   uv run python -c "import ideas.summarize_video; print('summarize_video: OK')"
   uv run python -c "import ideas.comments.generate_ideas; print('generate_ideas (comments): OK')"
   uv run python -c "import ideas.export_ideas_to_sheet; print('export_ideas: OK')"
   ```

4. **Verify CLI Help Flags**:
   ```bash
   ./phantom ideas generate-from-outlier --help
   ./phantom ideas generate-from-comments --help
   ./phantom yt summarize --help
   ```

5. **Dry Run Video Summarize & Idea Generation (Optional)**:
   Test with any public short YouTube video ID (e.g. `dQw4w9WgXcQ` or similar) to ensure transcript extraction, metadata fetching, and summarization run end-to-end without database errors.
