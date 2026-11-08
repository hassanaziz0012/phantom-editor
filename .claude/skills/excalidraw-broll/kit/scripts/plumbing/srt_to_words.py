#!/usr/bin/env python3
"""Convert a one-word-per-cue SRT (trimmed-1word.srt) into words.txt: one `start end word` line per word (seconds).

Usage: python3 srt_to_words.py trimmed-1word.srt research/words.txt
A cue holding several words is split evenly across its time span.
"""
import re, sys

TIME = re.compile(r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)')


def secs(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def convert(srt_path, out_path):
    out = []
    for block in re.split(r'\n\s*\n', open(srt_path, encoding='utf-8-sig').read().strip()):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            m = TIME.search(line)
            if m:
                a, b = secs(*m.groups()[:4]), secs(*m.groups()[4:])
                ws = ' '.join(lines[i + 1:]).split()
                step = (b - a) / max(1, len(ws))
                for k, w in enumerate(ws):
                    out.append('%.2f %.2f %s' % (a + k * step, a + (k + 1) * step, w))
                break
    with open(out_path, 'w') as f:
        f.write('\n'.join(out) + '\n')
    return len(out)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print('words', convert(sys.argv[1], sys.argv[2]))
