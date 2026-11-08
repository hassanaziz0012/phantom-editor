"""Phrase -> word timestamp lookup over words.txt (see srt_to_words.py).

    load('research/words.txt')
    T('two critical')              # start time of the first match
    T('hacked', after=1.5)         # first match at/after 1.5 s
    T('websites', end=True)        # end time of the last word of the match
    word_times('two critical')     # start time of each word in the match

Matching ignores case and punctuation (keeps % and $).
"""
import re

W = []  # (start, end, normalized word)


def _norm(word):
    return re.sub(r"[^a-z0-9%$]", '', word.lower())


def load(path):
    W.clear()
    for line in open(path, encoding='utf-8'):
        if line.strip():
            a, b, w = line.rstrip('\n').split(' ', 2)
            W.append((float(a), float(b), _norm(w)))
    return W


def _find(phrase, after):
    ws = [_norm(x) for x in phrase.split()]
    if not W:
        raise RuntimeError('anchors: no transcript loaded, call load(path) first')
    for i in range(len(W) - len(ws) + 1):
        if W[i][0] >= after - 1e-6 and all(W[i + k][2] == ws[k] for k in range(len(ws))):
            return i, len(ws)
    raise KeyError(f'phrase not found: {phrase!r} after {after}')


def T(phrase, after=0.0, end=False):
    i, n = _find(phrase, after)
    return W[i + n - 1][1] if end else W[i][0]


def word_times(phrase, after=0.0):
    i, n = _find(phrase, after)
    return [W[i + k][0] for k in range(n)]
