---
name: studio-analysis
description: Use when the user wants their YouTube channel or videos analysed rather than a single number, e.g. "audit my channel", "analyse my channel", "what's working and what isn't", "why are my views down", "why did my last videos flop", "what should I change", "what should I double down on", "give me channel recommendations / a strategy", "deep analysis of my Studio data". Runs the Opus studio-analyst agent on a full Studio dump. For quick lookups (one stat, one report) use the studio-data skill instead.
---

# Studio analysis

Deep channel analysis runs in the `studio-analyst` agent on Opus. Don't do the analysis yourself.

1. Spawn it with the Agent tool: `subagent_type: "studio-analyst"`, `model: "opus"` (always set it
   explicitly). Put the user's question in the prompt word for word, plus anything relevant they said
   earlier in the conversation (a video they're worried about, a goal, a period).
2. It takes a few minutes: it runs `phantom studio dump` (about 1 minute), builds a digest, reads it, may run
   a few more `phantom studio` commands, and returns the analysis as text. Its first line names the dump folder.
3. Save that text (without the `Dump:` line) to `<dump folder>/analysis.md` yourself; the agent can't write files.
4. Show the user the analysis in full (it's written for them), with the file path. Don't summarise it away.
   Follow-up questions about the same analysis: answer from `analysis.md` and `digest.md` in that folder,
   or send the question to the same agent.

The pieces, if you need to run them by hand:

```bash
phantom studio dump                                     # ~100 JSON files -> data/studio/<date_time>/
python3 .claude/skills/studio-analysis/digest.py [DIR]  # -> DIR/digest.md, ~35 KB, default newest dump
```

The analyst's method and report format are in `.claude/agents/studio-analyst.md`.
