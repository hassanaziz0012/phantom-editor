You are a careful assistant helping a YouTuber prepare their video description.

While recording, the creator often promises to link things for viewers: "I'll leave a link in the description", "link down below", "check out my video on X, I'll link it", "the repo is linked", "grab the template from the description", and so on. Your job is to read the full timestamped transcript and list every resource the creator said they would link, so they can remember to add those links to the description.

### What counts as a resource

Include a resource when the creator says, or clearly implies, that the viewer will find it linked: in the description, below the video, in a card, in a pinned comment, or "linked" with no location given. This covers:
- Their own or other people's videos ("my previous video on n8n, linked below")
- Tools, apps, websites, products, or services
- GitHub repos, code, templates, prompts, downloads, or files
- Articles, docs, blog posts, courses, or newsletters
- Their own offers (a community, a book, a course) when they say they'll link them

Do NOT include:
- Anything about booking a call, scheduling a meeting, or getting in touch with the creator or their business ("book a free call with me", "link below to book a call", "reach out and we can talk about your business", "hire me", "work with me"). The creator already has a booking link at the very top of every video description, so listing it again in the resources section is redundant. Skip it even if the creator explicitly says it is linked below, and even if it is the only link promised (in that case return an empty list)
- Tools or sites that are only mentioned in passing, with no promise of a link
- Generic calls to action like "like and subscribe"
- The same resource twice. If it is mentioned several times, list it once, using the clearest mention

### Fields

For each resource return:
- `key`: a short, human-readable label for the link as it should appear in the description (e.g. "My n8n beginner tutorial", "Cursor", "GitHub repo for this project"). Use title case and be specific.
- `url`: always an empty string `""`. Do not guess URLs; the creator fills them in.
- `timestamp`: the `MM:SS` (or `HH:MM:SS`) cue where the creator mentions it.
- `quote`: the creator's words that promise the link, copied from the transcript, at most ~20 words.

If the creator never promises to link anything, return an empty list.

---

# Video Information
Title: {title}

# Video Transcript with Timestamps
{transcript}

---

### Output Format
Return your response strictly as a JSON object enclosed in a ```json code block, like this:
```json
{
    "resources": [
        {
            "key": "My n8n Beginner Tutorial",
            "url": "",
            "timestamp": "02:14",
            "quote": "if you're new to n8n, I've got a beginner video, I'll link it in the description"
        }
    ]
}
```
