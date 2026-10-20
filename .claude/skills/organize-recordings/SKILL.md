---
name: organize-recordings
description: Move freshly recorded OBS videos from ~/Videos into their YouTube project folders (as raw.mp4, plus mistakes.txt when one exists), run the Phantom processing pipeline on the videos that have no mistakes file, and finish with a report table. Also cuts bad takes out of projects with mistakes: the user gives the time ranges to remove, they are saved as cuts.txt and the pipeline cuts them during its normal encode. Use when the user says which recorded video (by time) belongs to which project, gives time ranges to cut from a project, or asks to organize / process their new recordings.
---

# Organize recordings into YT projects

Follow the steps IN ORDER. Do not skip steps. Do not improvise extra actions.

## Which mode am I in? (decide this first)

The user's message can contain two kinds of requests. Handle each entry by its kind:

- **Mode A: new recording.** The user gives a clock time and a project ("12:32 pm goes to Use ALL your weekly quota"). Do Steps 1 to 7.
- **Mode B: cut ranges.** The user names a project and the time ranges to cut out of it ("for Model vs Harness cut 03:20-03:32 and 06:30-06:37"). The recording is already in the project folder as `raw.mp4`. **Skip Steps 1 to 4 completely** (no moving, no renaming) and go to **Step 4B**, then **Step 4C** (wait for the user's approval), then Steps 5 to 7.

One message can mix both: a new recording plus cut ranges for that same project ("12:32 pm goes to X, cut 02:05-02:17"). Do Steps 1 to 3 for it, then Step 4B, then Step 4C, then Steps 5 to 7.

If the user names no projects and gives no times, ask which projects to process. Do not scan and process everything on your own.

## Fixed facts

- Recordings folder: `~/Videos`
- Projects folder: `~/Videos/YT Projects` (one sub-folder per project, e.g. `~/Videos/YT Projects/How Prompt Caching works and how to use it`)
- Recording file names look like `2026-10-20 12-32-11.mp4` (date, then 24-hour `HH-MM-SS`).
- A mistakes file has the SAME name but ends in `.txt`, e.g. `2026-10-20 12-32-11.txt`.
- Inside a project folder the files must be named exactly `raw.mp4` and `mistakes.txt`.
- `mistakes.txt` only marks the moment the user noticed each mistake (`Mistake #1: 02:17`). It is NOT a cut list. Never turn it into cuts yourself.
- Cut ranges live in `cuts.txt` in the project folder, one range per line as `START-END` (e.g. `02:05-02:17`). Times are `MM:SS` or `HH:MM:SS`, fractions allowed on the seconds (`02:05.5`). The pipeline reads it automatically and removes those ranges from the final video.
- The processing command is `phantom projects process --raw raw.mp4 -y` (note: `projects`, plural; `-y` is required to skip the question). Run it from INSIDE the project folder.
- The folders `Asset Library`, `Excalidraw Scripts`, `loom-backup`, `Loom Outreach`, `YT Archive` and the file `sync.sh` in `~/Videos` are NOT recordings. Never touch them.

## Rules that are never broken

1. Never overwrite a file. Use `mv -n` and check the destination first. The only exception is `cuts.txt`, which you replace when the user gives new ranges for that project (Step 4B).
2. Never delete anything.
3. Never create a new project folder. If the project does not exist, ask the user.
4. Never guess. If something is ambiguous (two projects match, two recordings match the same minute), ask the user.
5. Run the processing command for ONE video at a time. Never two at once (it uses a lot of CPU).
6. Never process a project that has a `mistakes.txt` but no `cuts.txt`. Those wait for the user to give the ranges to cut (Mode B).
7. Only write ranges the user gave you, exactly as given. Never guess, widen, or derive ranges from `mistakes.txt`.
8. Never process a video whose `cuts.txt` you just wrote until the user has seen the saved file and approved it (Step 4C).
9. Do not git commit anything.

## Step 1: Read the user's mapping

The user tells you which recording belongs to which project, usually by clock time, for example:
"12:32 pm goes to Use ALL your weekly quota, 11:22 am goes to How Prompt Caching works".

Write the mapping down as a list: `time -> project name`.

Convert each time to 24-hour `HH-MM`:
- `12:32 pm` -> `12-32`
- `12:09 am` -> `00-09`
- `11:49 am` -> `11-49`
- `1:05 pm` -> `13-05`
- `9:30 am` -> `09-30`

## Step 2: Look at what is there

Run:

```bash
ls -la ~/Videos
ls ~/Videos/"YT Projects"
```

For each mapping entry:
1. Find the recording: the `.mp4` in `~/Videos` whose name contains ` HH-MM-` for that time (for example ` 12-32-` ). Exactly one file must match. If zero or more than one match, STOP and ask the user about that entry.
2. Find the project folder: the folder in `~/Videos/YT Projects` whose name matches the project name the user gave (ignore upper/lower case and small wording differences like "$0/month" vs "$0mo"). Exactly one folder must match. If none or several match, STOP and ask the user, listing the candidates.
3. Find the mistakes file: a `.txt` in `~/Videos` with the exact same name as the `.mp4` except for the extension. It may not exist. That is normal and means the video was recorded in one take. Match by FILE NAME only. Ignore any date or time written inside the txt.

Check the recording is finished (not still being written by OBS):

```bash
fuser ~/Videos/*.mp4 2>&1
```

If `fuser` prints a process id for a file you are about to move, skip that file and tell the user it is still recording.

Check the destination is free. These must NOT already exist in the project folder: `raw.mp4`, and (only if you are moving a txt) `mistakes.txt`:

```bash
ls -la "<project folder>"
```

If `raw.mp4` already exists there, STOP and ask the user for that video. Do not overwrite.

## Step 3: Move and rename

For each mapping entry, run (use the real file and folder names, keep the quotes, they contain spaces; a `$` in a folder name must be written as `\$` inside double quotes):

```bash
cd ~/Videos
mv -n "2026-10-20 12-32-11.mp4" "YT Projects/<project folder>/raw.mp4"
# ONLY if the matching .txt exists:
mv -n "2026-10-20 12-32-11.txt" "YT Projects/<project folder>/mistakes.txt"
```

Then verify with `ls -la "YT Projects/<project folder>"` that `raw.mp4` (and `mistakes.txt` if there was one) is there and the originals are gone from `~/Videos`.

## Step 4: Decide which projects to process

For each project you just organized, look in the project folder:

- `mistakes.txt` EXISTS and the user gave no cut ranges for this project -> status is **Organized only (waiting for cut ranges)**. Do NOT run processing. Read the file (`cat`) and remember its contents to show the user.
- `mistakes.txt` EXISTS and the user gave cut ranges for this project in the same message -> go to Step 4B for it.
- `mistakes.txt` does NOT exist -> status is **To process**.

## Step 4B: Write the cut ranges (Mode B)

For each project the user gave cut ranges for:

1. Find the project folder the same way as in Step 2 (exactly one match, otherwise ask the user).
2. List it: `ls -la "<project folder>"`. `raw.mp4` must exist. If it does not, status is **Skipped (no raw.mp4)**.
3. Check every range: it must be `START-END` with the end after the start. If one is unclear (missing end, end before start, "around 2:17"), STOP and ask the user for that project. Do not guess.
4. If `cuts.txt` already exists, `cat` it so the report can mention the old ranges. Then write the new file, replacing it, with exactly the user's ranges, one per line, in the order given:

```bash
cat > "<project folder>/cuts.txt" <<'EOF'
02:05-02:17
06:30-06:37
EOF
```

5. Print the file you just saved, exactly as it is on disk:

```bash
cat "<project folder>/cuts.txt"
```

6. Status is **Waiting for cuts approval**.

Do not touch `raw.mp4` or `mistakes.txt`. Do not add `-f`: the pipeline notices a new `cuts.txt` and redoes the steps that depend on it. The run overwrites the old `final.mp4`, `metadata.json` and other outputs in that folder. This is expected.

## Step 4C: Get the user's approval for the cuts (only if Step 4B ran)

STOP here before processing anything, including videos that have no cuts. End your turn with a short message that shows, for every project from Step 4B, the project name and the full output of `cat cuts.txt` in a code block, then asks: "Are these cuts correct? Reply yes to start processing."

When the user replies:
- They approve -> every **Waiting for cuts approval** project becomes **To process (with cuts)**. Continue with Step 5.
- They correct a range -> rewrite that project's `cuts.txt` with the corrected ranges (Step 4B, points 3 to 5) and do Step 4C again. Never process until they approve the current file.
- They drop a project -> mark it **Skipped (cuts not approved)** and continue with the rest. Do not delete its `cuts.txt`, but say in the report that the unapproved file is still in the folder and will be used if that project is ever processed.

## Step 5: Process, one video at a time

For each project marked **To process** or **To process (with cuts)**, in the order the user listed them, ONE AT A TIME:

1. Run this as a BACKGROUND command (set `run_in_background: true` and `timeout: 7200000`). The command is the same with or without cuts:

```bash
cd ~/Videos/"YT Projects/<project folder>" && phantom projects process --raw raw.mp4 -y > /tmp/process-<N>.log 2>&1; echo "exit=$?" >> /tmp/process-<N>.log
```

   (`<N>` = 1, 2, 3, ... so every video gets its own log file.)
2. Do NOT start the next video yet. Do NOT poll with `sleep`. Just end your turn with a one-line status; you will be notified when the background command finishes.
3. When notified, check the result:

```bash
tail -n 25 /tmp/process-<N>.log
```

   - Success = the last line is `exit=0` AND the log contains `COMPLETED SUCCESSFULLY`.
   - The log should show a `Manual cuts:` line for projects with cuts. If the run fails with a `cuts.txt line N:` error, the ranges were invalid: record **Failed** and show that error.
   - Anything else = failed. Record status **Failed**, keep the last 25 log lines for the report, and continue with the next video.
4. Only after the result is checked, start the next video.

A run takes roughly 5 to 8 minutes per video.

## Step 6: Collect the details

For each successfully processed project, read `<project folder>/summary.md`. It contains:
- a line like `Input: raw.mp4 (1920x1080, 30fps) -> final.mp4 (08:27)`: the number in brackets is the **final length**
- a `## Review flags` section: the **review flags** (or `none`)
- a `## Timing` section ending in `total 5m13s`: the **runtime**

Helpful commands:

```bash
grep -m1 'final.mp4 (' "<project folder>/summary.md"
awk '/^## Review flags/{f=1;next} /^## /{f=0} f' "<project folder>/summary.md"
grep -o 'total [0-9hms. ]*' "<project folder>/summary.md"
```

The recorded time comes from the original recording file name (for example `2026-10-20 12-32-11.mp4` -> 12:32 pm). Use the 12-hour format with am/pm in the report.

## Step 7: Report (REQUIRED, always do this)

When everything is finished, show the user ONE markdown table with ONE row per video, with exactly these columns:

| Recorded | Project | Mistakes file | Status | Final length | Runtime | Review flags |
|---|---|---|---|---|---|---|

- **Recorded**: date and time from the original file name, 12-hour format (e.g. `Oct 20, 12:32 pm`). For Mode B (cut ranges only) rows write `-`.
- **Project**: the project folder name.
- **Mistakes file**: `No` or `Yes`.
- **Status**: exactly one of `Processed`, `Processed (with cuts)`, `Organized only (waiting for cut ranges)`, `Failed`, `Skipped (reason)`. (`Waiting for cuts approval` is never a final status: the report only comes after Step 4C is resolved.)
- **Final length**: from summary.md, or `-` if not processed.
- **Runtime**: the pipeline total from summary.md, or `-` if not processed.
- **Review flags**: the flags from summary.md (type, timestamp range, confidence), `none`, or `-` if not processed.

Under the table add short notes:
- For every row with status `Organized only`: print the mistake markers from its `mistakes.txt` and say that processing was NOT run. Ask the user for the exact ranges to cut, as `START-END` (e.g. `02:05-02:17`), checked in their video player.
- For every `Processed (with cuts)` row: list the ranges that were cut (and the old ranges, if `cuts.txt` was replaced).
- For every failed video: the last lines of its log.
- Anything you skipped or asked about (still recording, no match, etc.).
- Any `.txt` files left in `~/Videos` that matched no recording, or recordings the user did not mention (leave them alone, just list them).

Only say "done" when every video in the user's mapping appears in the table. State facts as they are: do not say a video was processed unless Step 5 verified success.
