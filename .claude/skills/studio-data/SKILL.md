---
name: studio-data
description: Use for ANY question about the user's YouTube channel or video performance or YouTube Studio data, e.g. views, subscribers, CTR, impressions, retention, drop-off, watch time, traffic sources, search terms, audience, countries, demographics, Shorts, comments, thumbnail tests, "how did my last video do", "what should I make next". Answers by running `phantom studio` commands (live Studio numbers over HTTP).
---

# Studio data

Answer from `phantom studio <command>` output. All commands are read-only. Never open a browser for Studio data.

## 1. Find the video ID first

Any question about "my last/latest/newest video" or a video by title: run `phantom studio videos --json -n 20` first (newest first; add `--type shorts` for a Short) and take `video_id` from the matching row. Then run the video command. A YouTube URL also works as the argument.

Only do this when the question is about ONE specific video. Questions about the channel, "my videos" in general, or comparing videos need no lookup: use the channel command (no `--video`).

## 2. Pick the command

| The user asks about... | Run (`phantom studio ...`) |
|---|---|
| how a video did vs usual / vs my other videos | `typical --video <id> --json`, then `rank <id>` |
| channel better or worse than usual, or this period vs the one before ("last 30 days vs the 30 before") | `typical --json` (has `previous_period`; other lengths: `-p 7d`/`90d`/`365d`). Not `report overview`: it has no comparison |
| where viewers drop off / stop watching one video, its intro, hook | `retention <id> --json` |
| retention trend across videos, which videos hold viewers | `retention --channel --json` (compare `-p 28d` and `-p 90d` for a trend) |
| CTR, impressions, click-through from home/search/suggested | `funnel --json` (one video: `funnel --video <id> --json`) |
| compare or rank videos by CTR, impressions, views, watch time ("worst CTR", "best videos") | `report videos --json` (one row per video, has CTR). Never loop a command over every video |
| where views come from, by type (browse, search, suggested, external) | `report traffic --json` |
| which websites/apps outside YouTube send viewers | `report external --json` |
| which other videos send viewers via suggestions | `report suggested --json` |
| what people search to find me | `report search-terms --json` |
| what to make next, video ideas, content gaps | `research --json`, plus `report search-terms --json` |
| which videos gained subscribers | `report sub-videos --json` |
| where subscribers come from | `report sub-sources --json` |
| Shorts performance | `report shorts --json`; Shorts vs long-form: `report content-type --json` |
| views right now / today / last 48 hours | `report realtime --json`; last hour: `report realtime-minutes --json`; per video: `report realtime-videos --json` |
| views over time | `report daily --json` (or `weekly`, `monthly`) |
| channel totals (views, watch time, subs) for a period | `report overview --json` |
| countries, cities, age, gender, devices | `report countries` / `cities` / `age` / `gender` / `devices` (all `--json`) |
| subscribed vs not, new vs returning viewers | `report subscribed --json` / `report loyalty --json` |
| when my viewers are online, what else they watch | `audience --json` |
| comments, unanswered comments | `comments --json` (add `--unanswered`, `--video <id>`) |
| thumbnail / title A/B test (Test & Compare) | `videos --json -n 20`, read the video's `experiment` field |
| one video's details, lifetime totals | `video <id> --analytics` |
| engaged views, or views compared across 2026-08-24 (counting changed) | `query -d VIDEO -m EXTERNAL_VIEWS,ENGAGED_VIEWS --json` (or `-d MONTH`) |
| a breakdown no report covers (e.g. one video's daily views) | `query -d DIMS -m METRICS ... --json` (see below) |
| analyse/audit the channel, "what's working", "why are views down", recommendations, strategy | Don't answer from single commands: use the **studio-analysis** skill (Opus agent) |
| just save all the raw data | `dump` (writes JSON files to `data/studio/<date_time>/`) |
| list of all reports | `reports` |

A report can be scoped to videos: `report traffic -v <id> -p lifetime --json`. All 35 reports: `phantom studio reports`.

## 3. Rules

* **Add `--json`** to every command you read, EXCEPT `video`, `rank`, `vocab` and `dump`: they don't accept `--json` (`video` and `rank` already print JSON).
* **Prefer `report <name>` over `query`.** Use `query` only when no report fits. Check names with `phantom studio vocab metrics` / `vocab dimensions` first. Example, one video's daily views over its life: `query -d DAY -m EXTERNAL_VIEWS -v <id> -p lifetime --json`.
* **Periods (`-p`):**
  * `report` and `query`: `7d`, `28d` (default), `90d`, `365d`, `48h`, `60m`, `lifetime`, a year `2026`, a month `2026-11`, or a range `2026-11-01:2026-11-19`.
  * `typical`, `funnel`, `audience`, `retention --channel`: only `7d`, `28d`, `60d`, `90d`, `365d`, `lifetime`.
  * There is no `30d`: use `28d`. "This year" = `2026` for report/query. "All time" = `lifetime`.
* **Typical ranges** exist only for 28 days (channel) or since publish (video), and only for views, watch time, average view duration and subscribers. Empty elsewhere is expected; use `previous_period` instead.
* **Views vs engaged views:** "views" means `external_views` (the public count). `query`'s `VIEWS` is NOT that: it returns engaged views (stayed past the opening seconds), same as `ENGAGED_VIEWS`. Average view duration and CTR are per engaged view. `external_views` counts from the first frame since 2026-08-24, so compare across that date with `ENGAGED_VIEWS`. `query` and `report` print what their metrics mean after the output.
* **Units:** `_sec` fields are seconds. Percentages are 0–100. CTR is `video_thumbnail_impressions_vtr` (4.2 = 4.2%).
* **Missing data:** `-` / `null` means Studio withheld it (too few viewers), not zero. `not_enough_data` is a real answer: report it, don't retry.
* **Login errors** (401/403, "not logged in"): run `phantom studio login`, then retry once.
* **Older commands:** `phantom yt info <id>` and `phantom yt channel-stats` are fine for a quick single-video or channel summary. For anything more, use `studio`.

Anything else (flags, output fields, limits): read `docs/studio.md`.
