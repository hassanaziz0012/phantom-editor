# YouTube Studio

`phantom studio` reads everything in YouTube Studio (content, analytics, comments) with plain HTTPS requests. It doesn't use a browser or the YouTube API. The numbers are the live ones Studio shows, so there's no 2–3 day API lag.

Code: [`youtube_api/studio/`](../youtube_api/studio/).

## How it works

Studio's web app gets its data from an internal API at `studio.youtube.com/youtubei/v1/...`. Those requests only need the account's Google cookies, plus an `Authorization: SAPISIDHASH ...` header built from one of them. The commands make the same requests directly.

* **Login, once:** `phantom studio login` copies the cookies from the logged-in p9222 Chrome profile into `~/.config/phantom/studio_session.json` (mode 600, outside the repo). If that Chrome isn't running, it's started headless, read, and closed again.
* **After that, no browser.** Google rotates some cookies, and every response's `Set-Cookie` is saved back, which keeps the session alive. If Studio rejects the session anyway (HTTP 401/403), the cookies are re-imported from Chrome automatically and the request is retried once.
* **Client version and channel ID** are read from Studio's home page over HTTP once a day.

| Endpoint | Used for |
|---|---|
| `yta_web/join` | Every analytics query: any dimensions × metrics × filters × date range (Studio's Advanced Mode engine) |
| `yta_web/get_cards` | Studio's analytics cards: retention with typical band and key moments, typical ranges, funnel, ranking, audience, Research feed |
| `creator/list_creator_videos` | Videos, Shorts and live tabs (including drafts, private and scheduled) |
| `creator/get_creator_videos` | One video's full record |
| `creator/list_creator_playlists` | Playlists and podcasts |
| `comment/get_comments` | Comments and the unanswered inbox |

## Commands

Every command prints a table, or JSON with `--json`. Video arguments take IDs or any YouTube/Studio URL.

```bash
phantom studio login                       # once
phantom studio whoami                      # check the session still works
```

### Content

```bash
phantom studio videos                      # long-form videos, newest first
phantom studio videos --type shorts        # videos | shorts | live | all
phantom studio video <id> --analytics      # full details + lifetime totals (JSON)
phantom studio video <id> --raw            # every field Studio has
phantom studio playlists [--podcasts]
phantom studio comments [--video <id>] [--unanswered] [--order relevant] [--limit 100]
```

A video's `experiment` field shows a running or finished Test & Compare (thumbnail/title A/B test): its state, result, winner and each variant's title.

### Analytics reports

```bash
phantom studio reports                     # list them all
phantom studio report traffic              # channel, last 28 days
phantom studio report traffic --video <id> --period lifetime
phantom studio report search-terms --period 90d
```

| Report | What |
|---|---|
| `overview` | Totals: impressions, CTR, views, watch time, avg view duration/%, unique viewers, likes, comments, shares, subscribers |
| `daily` / `weekly` / `monthly` | The same over time |
| `videos` / `long-form` / `shorts` / `live` | Content ranked by views, with impressions and CTR (Shorts: feed impressions and swipe-through rate) |
| `content-type` | Long-form vs Shorts vs live |
| `traffic` | Traffic source types, with impressions and CTR each |
| `search-terms` / `suggested` / `external` | YouTube search terms, suggesting videos, external sites |
| `playlists` | Playlist views, starts, watch time, saves |
| `countries` / `cities` / `age` / `gender` | Geography and demographics |
| `devices` / `os` / `playback` | Device type, OS, where it was watched |
| `subscribed` / `loyalty` | Subscribers vs not; new, casual and regular viewers |
| `sub-sources` / `sub-videos` | Where subscribers came from, which videos gained them |
| `sharing` / `end-screens` / `cards` | Share targets, end screen and info card clicks |
| `captions` / `audio-tracks` | Views by subtitle and audio language |
| `posts` | Community posts |
| `revenue` | Earnings (monetized channels only) |
| `realtime` / `realtime-videos` / `realtime-minutes` | Last 48 hours by hour or video, last 60 minutes by minute |

**Periods** (`--period`): `7d`, `28d` (default), `90d`, `365d` (whole days up to yesterday, like Studio), `48h`, `60m`, `lifetime`, `2026`, `2026-11`, or `2026-11-01:2026-11-19`.

**Scope:** the channel by default; `--video id1,id2` or `--playlist <id>` narrow it, and `--filter DIMENSION=value[,value]` adds any filter, e.g. `--filter CREATOR_CONTENT_TYPE=SHORTS`.

### Any query

`query` is Studio's Advanced Mode: any metrics by any dimensions.

```bash
phantom studio query --dims DAY,TRAFFIC_SOURCE_TYPE --metrics EXTERNAL_VIEWS,VIDEO_THUMBNAIL_IMPRESSIONS_VTR --period 7d
phantom studio query --dims VIDEO --metrics SUBSCRIBERS_NET_CHANGE --order SUBSCRIBERS_NET_CHANGE --limit 10
phantom studio query --dims DAY,VIDEO --metrics EXTERNAL_VIEWS --period lifetime --limit 0   # every video's daily views
phantom studio vocab metrics               # all 196 metric names
phantom studio vocab dimensions            # all 143 dimension names
```

* `METRIC%` gives a metric as a share of the total (how Studio shows demographics).
* `--limit 0` returns all rows (up to 10,000; Studio doesn't page these results).
* Not every metric/dimension pair is supported. Studio answers those with HTTP 400 and the name it rejected.

### Retention, typical ranges and Studio's comparisons

These read Studio's own analytics cards (`yta_web/get_cards`), so they include the comparisons Studio computes itself.

```bash
phantom studio retention <id>              # curve vs the typical band, key moments, chapters
phantom studio retention --channel         # every recent long-form video + channel averages
phantom studio typical                     # channel totals vs typical range and previous period
phantom studio typical --video <id> --series   # a video since publish, with the hourly typical band
phantom studio funnel [--video <id>]       # impressions -> CTR -> views -> watch time, per surface
phantom studio rank <id>                   # "ranks 1 of 10" against your recent uploads
phantom studio audience                    # when your viewers are on YouTube, what else they watch
phantom studio research                    # Research tab: audience searches (volume, content gaps), relevant videos
```

* **`retention <id>`**
    * The curve has 100 points, each with `retention_pct`, the typical band for comparable videos (`typical_low`–`typical_high`), and `vs_typical` (above/within/below).
    * The summary adds `retention_at_30s` (Studio's "still watching at 0:30") next to its typical range, and counts how many points are above or below typical.
    * `key_moments` lists the intro (scored against your average), dips, step-downs and spikes. `chapters` lists the video's chapters.
* **`retention --channel`**: the same summary for each video with views in the period, plus channel averages for % watched, average view duration and 30-second retention, and how many videos are mostly above or below typical.
* **`typical`**: Studio's "typical" is the usual range for your channel (last 28 days) or for comparable videos at the same age (since publish).
    * Studio computes it only for some metrics: views, watch time, average view duration, and channel subscribers. Other metrics show `-`.
    * Other periods (`7d`, `90d`, `365d`, `lifetime`) have no typical range, but do have `previous_period`.
* **`audience`** and parts of `retention --channel` need a minimum audience. When Studio shows "Not enough data", these print `not_enough_data`.

Card periods: `7d`, `28d` (default), `60d`, `90d`, `365d`, `lifetime`. A video defaults to since publish.

### Everything at once

```bash
phantom studio dump                        # → data/studio/<date_time>/ (gitignored)
phantom studio dump --periods 7d 28d 365d lifetime --retention 20
```

`dump` saves all of these as JSON:

* the content lists, playlists and newest comments
* every report for each period, plus the realtime reports
* every video's views per day since publish (`video_daily_lifetime.json`), to compare videos at the same age
* the typical comparison, funnel and channel retention for each period
* research and audience
* retention, typical (with the hourly series), funnel and rank for the newest public videos

It runs 8 requests in parallel (`--workers`) and takes about a minute. `_meta.json` records the time and any reports that failed.

## Output conventions

* Columns are Studio's metric names, lowercased.
* Durations come in milliseconds and are converted to seconds, with a `_sec` suffix (`average_watch_time_sec`).
* Percentages are 0–100: CTR (`video_thumbnail_impressions_vtr`) 4.2 means 4.2%.
* `-` / `null` means Studio withheld the value, usually because there are too few viewers for privacy.

## Limits

* Studio takes about 2 s per analytics query, whatever the size.
* `TRAFFIC_SOURCE_DETAIL` without a `TRAFFIC_SOURCE_TYPE` filter over long periods returns HTTP 500. The ready-made reports always filter it.
* Per-variant Test & Compare numbers aren't exposed until Studio shows results.
* Not read yet: the recent-subscribers list, copyright claims, the Earn page, community post text, and comment like counts.
* This is Studio's private API, so it can change without notice. Metric and dimension names come from Studio's JS bundle (see `vocab.py` for how to refresh them).
