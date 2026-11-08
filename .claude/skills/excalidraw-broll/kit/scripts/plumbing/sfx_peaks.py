#!/usr/bin/env python3
"""Measure every SFX file once: real length (ffprobe) + peak offset (`npx hyperframes audio --json`).

Usage (from the project root): python3 scripts/plumbing/sfx_peaks.py assets/sfx research/sfx-peaks.json
Output: {"whoosh": {"file": "assets/sfx/whoosh.mp3", "duration": 0.94, "peak": 0.41}, ...}
Re-run whenever you add or edit SFX files.
"""
import json, os, subprocess, sys

EXTS = ('.mp3', '.wav', '.m4a', '.ogg')


def length(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def peak(path):
    out = subprocess.run(['npx', '--yes', 'hyperframes', 'audio', path, '--json'],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)['items'][0]['moments']['peak'])


def measure(sfx_dir):
    res = {}
    for f in sorted(os.listdir(sfx_dir)):
        name, ext = os.path.splitext(f)
        if ext.lower() in EXTS:
            p = os.path.join(sfx_dir, f)
            res[name] = dict(file=p, duration=round(length(p), 3), peak=peak(p))
            print('%-24s dur %.3f peak %.3f' % (name, res[name]['duration'], res[name]['peak']))
    return res


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    json.dump(measure(sys.argv[1]), open(sys.argv[2], 'w'), indent=1)
