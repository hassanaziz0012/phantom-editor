#!/usr/bin/env python3
import os
import re
import sys
import time
import json
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from the repository root's .env file
repo_root = Path(__file__).resolve().parent.parent
load_dotenv(repo_root / ".env")
load_dotenv() # Fallback to loading from current working directory or process environment


from utils import Colors, colorize, parse_time_to_seconds as _parse_time_to_seconds, format_seconds_to_hhmmss, format_time

parse_time_to_seconds = lambda s: _parse_time_to_seconds(s, default=None)
format_time_full = lambda s: format_time(s, separator=",")


def parse_transcript(file_path: str) -> list:
    """Parses a transcript file supporting standard SRT format and bracketed format.
    Returns a list of dicts: [{'start': float, 'end': float, 'text': str}]
    """
    segments = []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Try bracketed format first: [00:00:00,000 -> 00:00:05,120] text
    bracket_pattern = re.compile(
        r'^\[\s*([0-9:,\.]+)\s*(?:->|-->)\s*([0-9:,\.]+)\s*\]\s*(.*)$'
    )
    
    lines = content.splitlines()
    matched_brackets = False
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue
        match = bracket_pattern.match(line_str)
        if match:
            start_str, end_str, text = match.groups()
            try:
                start_sec = parse_time_to_seconds(start_str)
                end_sec = parse_time_to_seconds(end_str)
                segments.append({
                    "start": start_sec,
                    "end": end_sec,
                    "text": text.strip()
                })
                matched_brackets = True
            except ValueError:
                pass
                
    if matched_brackets:
        return segments

    # Fallback to standard SRT parser
    content_normalized = content.replace('\r\n', '\n')
    blocks = content_normalized.split('\n\n')
    
    for block in blocks:
        block_lines = [l.strip() for l in block.split('\n') if l.strip()]
        if len(block_lines) >= 3:
            if '-->' in block_lines[1]:
                time_part = block_lines[1]
                times = time_part.split('-->')
                if len(times) == 2:
                    try:
                        start_sec = parse_time_to_seconds(times[0])
                        end_sec = parse_time_to_seconds(times[1])
                        text = " ".join(block_lines[2:])
                        segments.append({
                            "start": start_sec,
                            "end": end_sec,
                            "text": text.strip()
                        })
                    except ValueError:
                        pass
                        
    return segments

def consolidate_segments(segments: list) -> list:
    """Consolidates Whisper micro-segments into logical semantic blocks.
    Groups segments until a pause > 1.5s is hit, or the current segment ends a sentence.
    """
    if not segments:
        return []
        
    consolidated = []
    current_block = {
        "start": segments[0]["start"],
        "end": segments[0]["end"],
        "texts": [segments[0]["text"]]
    }
    
    sentence_endings = ('.', '?', '!')
    
    for seg in segments[1:]:
        pause = seg["start"] - current_block["end"]
        
        ends_sentence = False
        if current_block["texts"]:
            last_text = current_block["texts"][-1].strip()
            if last_text and last_text[-1] in sentence_endings:
                ends_sentence = True
                
        # Consolidate if pause > 1.5s or previous segment ended a sentence,
        # or current block size is not excessively long (e.g. 30 seconds max).
        if pause > 1.5 or ends_sentence or (seg["start"] - current_block["start"] > 30.0):
            consolidated.append({
                "start_time": current_block["start"],
                "end_time": current_block["end"],
                "text": " ".join(current_block["texts"])
            })
            current_block = {
                "start": seg["start"],
                "end": seg["end"],
                "texts": [seg["text"]]
            }
        else:
            current_block["end"] = seg["end"]
            current_block["texts"].append(seg["text"])
            
    if current_block:
        consolidated.append({
            "start_time": current_block["start"],
            "end_time": current_block["end"],
            "text": " ".join(current_block["texts"])
        })
        
    return consolidated

def get_embeddings(texts: list, api_key: str) -> list:
    """Fetches text embeddings in batches from Google Gemini API using model gemini-embedding-2."""
    batch_size = 100
    embeddings = []
    
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        
        requests_list = []
        for txt in chunk:
            requests_list.append({
                "model": "models/gemini-embedding-2",
                "content": {
                    "parts": [{"text": txt}]
                }
            })
            
        payload = {"requests": requests_list}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:batchEmbedContents?key={api_key}"
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                if "embeddings" in res_data:
                    for emb in res_data["embeddings"]:
                        embeddings.append(emb["values"])
                else:
                    raise KeyError("Response from Gemini API does not contain 'embeddings' key.")
        except urllib.error.HTTPError as e:
            # Handle HTTP errors gracefully without exposing sensitive credentials or keys in traceback
            error_body = e.read().decode("utf-8") if e.fp else ""
            sys.stderr.write(f"HTTP Error calling Gemini Embedding API: {e.code} - {e.reason}\nBody: {error_body}\n")
            raise e
        except Exception as e:
            sys.stderr.write(f"Error calling Gemini Embedding API: {e}\n")
            raise e
            
    return embeddings

def cosine_similarity(v1: list, v2: list) -> float:
    """Calculates Cosine Similarity between two high-dimensional vectors."""
    dot_product = sum(x * y for x, y in zip(v1, v2))
    norm1 = sum(x * x for x in v1) ** 0.5
    norm2 = sum(x * x for x in v2) ** 0.5
    if not norm1 or not norm2:
        return 0.0
    return dot_product / (norm1 * norm2)

def verify_duplicates_batch_with_llm(pairs: list, api_key: str) -> tuple[list | None, str]:
    """Queries Gemini (gemini-3.5-flash) using structured JSON output to analyze
    a batch of potential duplicate pairs and decide which ones are bad takes.
    Returns (decisions_list, error_message).
    """
    model = "gemini-3.5-flash"
    
    prompt = (
        "You are an expert video editing assistant. You are analyzing multiple pairs of close "
        "video transcript segments to identify 'bad takes' (duplicate attempts/corrections of the same line or thought).\n\n"
        "In video recording, a speaker often stumbles or wants to rephrase a line. They will stop, pause, "
        "and then repeat/correct the line. The earlier attempt (the 'bad take') must be deleted, while the "
        "final attempt (the 'keeper') must be kept.\n\n"
        "For each pair below, determine if Segment A is a duplicate/stumbled/repeated attempt of Segment B (which is the keeper).\n"
        "Guidelines:\n"
        "- Respond true for 'is_bad_take' ONLY if Segment A is a failed, partial, or duplicate attempt of Segment B, "
        "and Segment B is the correction/keeper. They should express the same core line/thought.\n"
        "- Respond false for 'is_bad_take' if Segment A and Segment B are different sentences, sequential points, "
        "or part of a continuous explanation/topic where BOTH should be kept in the video.\n"
        "- Respond false for 'is_bad_take' if they are just normal conversation flow (e.g. moving from one topic "
        "to the next, like from intro to database).\n\n"
        "Analyze the following list of pairs and return the decision for each pair index:\n\n"
    )
    
    for idx, p in enumerate(pairs):
        prompt += (
            f"Pair Index {idx}:\n"
            f"  Segment A (Earlier, at {p['anchor_time']}): \"{p['anchor_text']}\"\n"
            f"  Segment B (Later, at {p['candidate_time']}): \"{p['candidate_text']}\"\n\n"
        )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "decisions": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "pair_index": {
                                    "type": "INTEGER",
                                    "description": "The 0-based index of the pair being evaluated."
                                },
                                "is_bad_take": {
                                    "type": "BOOLEAN",
                                    "description": "True if Segment A of this pair is a duplicate/stumbled/repeated attempt of Segment B and should be cut. False otherwise."
                                },
                                "reason": {
                                    "type": "STRING",
                                    "description": "Brief explanation of the decision."
                                }
                            },
                            "required": ["pair_index", "is_bad_take", "reason"]
                        }
                    }
                },
                "required": ["decisions"]
            }
        }
    }
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        # Timeout of 90 seconds to allow the API to generate response for multiple pairs
        with urllib.request.urlopen(req, timeout=90) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if "candidates" in res_data and res_data["candidates"]:
                candidate_node = res_data["candidates"][0]
                if "content" in candidate_node and "parts" in candidate_node["content"]:
                    text_response = candidate_node["content"]["parts"][0]["text"]
                    result = json.loads(text_response.strip())
                    return result.get("decisions", []), ""
            return None, f"Malformed response from API: {json.dumps(res_data, indent=2)}"
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        return None, f"HTTP Error {e.code}: {e.reason}\nResponse Body:\n{error_body}"
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return None, f"Exception occurred:\n{e}\n\nTraceback:\n{tb}"


def main():
    parser = argparse.ArgumentParser(description="Find and report duplicate (bad) takes in a video transcript.")
    parser.add_argument("transcript_path", help="Path to the transcript file (.srt).")
    parser.add_argument("--window", "-w", type=float, default=90.0, help="Rolling comparison time window in seconds (default: 90.0).")
    parser.add_argument("--threshold", "-t", type=float, default=0.78, help="Cosine similarity threshold for duplicates (default: 0.78).")
    parser.add_argument("--output", "-o", help="Path to export JSON metadata of cut zones (default: same folder as transcript with _badtakes.json suffix).")
    
    args = parser.parse_args()
    
    # Security check: Sanitizing file paths
    # Resolve the path fully to handle symbolic links and check traversal
    transcript_file = os.path.abspath(args.transcript_path)
    
    if not os.path.isfile(transcript_file):
        print(colorize(f"Error: File not found at {transcript_file}", Colors.FAIL))
        sys.exit(1)
        
    # Read GEMINI_API_KEY from environment
    # TODO(security): API key loaded exclusively from env to avoid hardcoded secrets.
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(colorize("Error: GEMINI_API_KEY environment variable is not set. Please set it before running this script.", Colors.FAIL))
        sys.exit(1)
        
    print(colorize("Parsing transcript...", Colors.BOLD + Colors.BLUE))
    segments = parse_transcript(transcript_file)
    if not segments:
        print(colorize("Error: No valid subtitle or bracketed segments found in the transcript file.", Colors.FAIL))
        sys.exit(1)
        
    print(f"Loaded {len(segments)} transcript segments.")
    
    print(colorize("Consolidating segments...", Colors.BOLD + Colors.BLUE))
    blocks = consolidate_segments(segments)
    print(f"Consolidated into {len(blocks)} logical semantic blocks.")
    
    print(colorize("Generating embeddings via Gemini API (gemini-embedding-2)...", Colors.BOLD + Colors.BLUE))
    texts = [b["text"] for b in blocks]
    try:
        embeddings = get_embeddings(texts, api_key)
    except Exception as e:
        print(colorize("Error: Failed to fetch embeddings from Gemini API. Check your connection or API key.", Colors.FAIL))
        sys.exit(1)
        
    for i, emb in enumerate(embeddings):
        blocks[i]["embedding"] = emb
        
    print(colorize("Analyzing timeline for duplicate takes...", Colors.BOLD + Colors.BLUE))
    to_remove_indices = set()
    keepers = {} # map anchor index -> keeper index
    similarities = {} # map anchor index -> similarity score
    reasons_map = {} # map anchor index -> reason description
    
    # 1. Collect potential duplicate pairs
    pairs = []
    pair_meta = []
    
    for i in range(len(blocks)):
        anchor = blocks[i]
        for j in range(i + 1, len(blocks)):
            candidate = blocks[j]
            time_diff = candidate["start_time"] - anchor["start_time"]
            if time_diff > args.window:
                break
                
            sim = cosine_similarity(anchor["embedding"], candidate["embedding"])
            if sim >= args.threshold:
                anchor_time = format_seconds_to_hhmmss(anchor["start_time"])
                candidate_time = format_seconds_to_hhmmss(candidate["start_time"])
                
                pairs.append({
                    "anchor_time": anchor_time,
                    "anchor_text": anchor["text"],
                    "candidate_time": candidate_time,
                    "candidate_text": candidate["text"]
                })
                pair_meta.append({
                    "i": i,
                    "j": j,
                    "sim": sim
                })

    if pairs:
        print(f"Found {len(pairs)} potential duplicate pairs based on embedding similarity.")
        print(colorize(f"Sending batch request with all {len(pairs)} pairs to Gemini (gemini-3.5-flash)...", Colors.BOLD + Colors.BLUE))
        
        decisions, error_msg = verify_duplicates_batch_with_llm(pairs, api_key)
        
        if decisions is None:
            print(colorize(f"\n[ERROR] LLM batch call failed. Exiting because the frontier LLM is required for bad take detection.", Colors.FAIL))
            print(colorize(f"Error Details:\n{error_msg}", Colors.FAIL))
            sys.exit(1)
            
        # Build map from pair_index to decision
        decisions_map = {}
        for d in decisions:
            try:
                p_idx = int(d.get("pair_index", -1))
                if p_idx >= 0:
                    decisions_map[p_idx] = d
            except (ValueError, TypeError):
                continue
                
        # Process the evaluations in order
        for idx, meta in enumerate(pair_meta):
            i = meta["i"]
            j = meta["j"]
            sim = meta["sim"]
            
            # If the anchor block has already been marked as a bad take, we skip evaluating it further
            if i in to_remove_indices:
                continue
                
            decision = decisions_map.get(idx)
            if not decision:
                print(colorize(f"Warning: No decision returned by LLM for pair index {idx}. Skipping.", Colors.WARNING))
                continue
                
            is_bad_take = decision.get("is_bad_take", False)
            reason = decision.get("reason", "No reason provided")
            
            anchor_time = format_seconds_to_hhmmss(blocks[i]["start_time"])
            candidate_time = format_seconds_to_hhmmss(blocks[j]["start_time"])
            
            print(f"   - Evaluating [{anchor_time}] vs [{candidate_time}] (embedding sim: {sim:.4f})...")
            if is_bad_take:
                print(colorize(f"     ❌ DUPLICATE CONFIRMED: {reason}", Colors.FAIL))
                to_remove_indices.add(i)
                keepers[i] = j
                similarities[i] = sim
                reasons_map[i] = reason
            else:
                print(colorize(f"     ✨ KEEP BOTH: {reason}", Colors.GREEN))
    else:
        print("No potential duplicate pairs found based on similarity threshold.")
                    
                    
    # Build list of raw bad takes chunks
    raw_bad_takes = []
    for i in sorted(list(to_remove_indices)):
        block = blocks[i]
        keeper_idx = keepers[i]
        keeper_block = blocks[keeper_idx]
        keeper_time_str = format_seconds_to_hhmmss(keeper_block["start_time"])
        raw_bad_takes.append({
            "start": block["start_time"],
            "end": block["end_time"],
            "text": block["text"],
            "keeper_start": keeper_block["start_time"],
            "keeper_text": keeper_block["text"],
            "similarity": similarities[i],
            "reason": reasons_map.get(i, f"Duplicate of take at {keeper_time_str}")
        })
        
    # Merge overlapping or adjacent bad takes into continuous Cut Zones
    raw_bad_takes.sort(key=lambda x: x["start"])
    merged_zones = []
    for chunk in raw_bad_takes:
        if not merged_zones:
            merged_zones.append({
                "start": chunk["start"],
                "end": chunk["end"],
                "reasons": [chunk["reason"]]
            })
        else:
            last = merged_zones[-1]
            # Merge if they overlap or are very close (gap <= 1.0s)
            if chunk["start"] <= last["end"] + 1.0:
                last["end"] = max(last["end"], chunk["end"])
                if chunk["reason"] not in last["reasons"]:
                    last["reasons"].append(chunk["reason"])
            else:
                merged_zones.append({
                    "start": chunk["start"],
                    "end": chunk["end"],
                    "reasons": [chunk["reason"]]
                })
                
    # Format Cut Zones for export
    cut_zones = []
    for zone in merged_zones:
        cut_zones.append({
            "cut_start": round(zone["start"], 2),
            "cut_end": round(zone["end"], 2),
            "reason": "; ".join(zone["reasons"])
        })
        
    # Print beautiful terminal report
    print("=" * 80)
    print(colorize("DUPLICATE-TAKE DETECTOR AUDIT REPORT", Colors.BOLD + Colors.HEADER))
    print("=" * 80)
    print(f"File Path:            {transcript_file}")
    print(f"Similarity Threshold: {args.threshold}")
    print(f"Rolling Window:       {args.window} seconds")
    print(f"Total Blocks:         {len(blocks)}")
    print(f"Detected Bad Takes:   {len(to_remove_indices)} blocks")
    print(f"Merged Cut Zones:     {len(cut_zones)} zones")
    print("-" * 80)
    
    if raw_bad_takes:
        print(colorize("DETAILS OF DETECTED DUPLICATE TAKES:", Colors.BOLD + Colors.WARNING))
        for item in raw_bad_takes:
            start_str = format_time_full(item["start"])
            end_str = format_time_full(item["end"])
            k_start_str = format_time_full(item["keeper_start"])
            print(f"\n[{start_str} --> {end_str}] (Duration: {item['end'] - item['start']:.2f}s)")
            print(colorize(f" ❌ BAD TAKE:   \"{item['text']}\"", Colors.FAIL))
            print(colorize(f" ✨ KEEPER:     (at {k_start_str}) \"{item['keeper_text']}\"", Colors.GREEN))
            print(f"   Similarity:  {item['similarity']:.4f}")
            print(f"   Reason:      {item['reason']}")
        print("-" * 80)
        
        print(colorize("PROPOSED TIMELINE CUT ZONES (TO REMOVE):", Colors.BOLD + Colors.WARNING))
        total_cut_duration = 0.0
        for zone in cut_zones:
            duration = zone["cut_end"] - zone["cut_start"]
            total_cut_duration += duration
            start_formatted = format_time_full(zone["cut_start"])
            end_formatted = format_time_full(zone["cut_end"])
            print(f" • Cut Zone: {start_formatted} --> {end_formatted} (Duration: {duration:.2f}s) - {zone['reason']}")
            
        print("-" * 80)
        print(colorize(f"SUMMARY: Total of {len(cut_zones)} zone(s) proposed for deletion. Total duration to remove: {total_cut_duration:.2f}s.", Colors.BOLD + Colors.BLUE))
    else:
        print(colorize("🎉 No duplicate takes found. The transcript looks clean!", Colors.BOLD + Colors.GREEN))
        
    # Export to JSON metadata file
    if args.output:
        output_file = os.path.abspath(args.output)
    else:
        folder = os.path.dirname(transcript_file)
        basename = os.path.splitext(os.path.basename(transcript_file))[0]
        output_file = os.path.join(folder, f"{basename}_badtakes.json")
        
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(cut_zones, f, indent=2)
        print(colorize(f"Exported cut zones metadata to: {output_file}", Colors.GREEN))
    except Exception as e:
        print(colorize(f"Error writing metadata to {output_file}: {e}", Colors.FAIL))
        
    print("=" * 80)

if __name__ == "__main__":
    main()
