You help me pick my next long-form YouTube video. Below are the posts taking off in my X home feed right now. Find the stories in them that I should make a video about in the next 24–72 hours, and package each one so it gets clicked.

# My channel

I make videos about AI, coding with AI, AI tools and models, and where tech and the future are heading, for developers and people who build with AI. Typical video: me on camera giving a strong take, sometimes with a quick demo or test, 5–10 minutes.

What the numbers say:
- My hits all won on the YouTube home feed (Browse) by being **a named thing people already care about this week, plus one strong, simple take on it.** The topic was hot at the time. Those videos clicked at 4–5.5% on Browse; my usual video gets about 2.6% and dies after a few hundred impressions.
- A hot topic's run lasts about 3–4 days. A sequel to a hit, made a month later, flopped. Freshness matters more than the topic.
- Tutorials, coding follow-alongs and long builds don't work for me anymore and I don't make them. Don't suggest them.
- Vague titles about "AI" in general do worse than titles that name the specific thing (a model, a company, a tool, a person, a result).

My best videos, by home-feed performance:
{channel_hits}

My most recent uploads (don't repeat these):
{recent_videos}

# Posts from my feed

Each post: trend score (higher = taking off faster than usual), age, likes, views, engagement per hour, author and follower count, then the text. Quoted posts are indented.

My feed also has memes, politics and random viral stuff. Ignore anything that isn't about AI, tech, software, science/futurism breakthroughs, or the people and companies behind them. A post that's only a joke about a real story still counts as evidence the story is hot.

{posts}

# What to do

1. Group the relevant posts into stories. One story can span many posts; several big accounts posting about the same thing is the strongest signal.
2. Pick the 3–5 best stories for a video from me, ranked. Judge them on: how hot the story is right now and whether it'll still be hot when my video is out in 1–3 days; whether a wide audience (not just insiders) cares; whether I can say something with a clear opinion; and whether it fits my channel. If only 2 are any good, return 2.
3. For each, give one take: a clear one-sentence opinion I could argue for 8 minutes. No fence-sitting.
4. And one title, written the way my hits are written: under 60 characters, names the specific thing, creates a gap or a stake. No clickbait I can't pay off, no emoji, no colon-and-subtitle titles.

Return only a JSON list, best first:
```json
[
  {
    "story": "what happened, in under 15 words",
    "timing": "rising, peaking or fading, and when to film, e.g. 'peaking, film within 36h'",
    "take": "one-sentence opinion",
    "title": "the title",
    "url": "the single most important post"
  }
]
```
