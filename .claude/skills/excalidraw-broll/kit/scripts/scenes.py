"""THIS VIDEO's storyboard + scenes. The only file you write per video (plus, rarely, a new doodle).

Contract:
  * board() returns [(start_seconds, scene_fn or F, kwargs)] in time order. F (= None) means "face only".
    A scene lasts until the next entry starts. First entry is usually (0.0, F, {}).
  * A scene fn is fn(c, **kw) -> k.done(); kw carries prev_face / next_face (set by register()).
  * B-roll scenes show the user's face in a rounded square at the bottom right by default: keep content out
    of x > 1480 and y > 640 (k.face is True). Add face=False to a board entry for B-roll only, full canvas.
  * All times are ABSOLUTE seconds from the transcript: T('phrase') (first match in the video) or
    A(c, 'phrase') (first match from just before this scene's start - use this inside scenes).
  * Every element id passed to k.* must be unique within the scene.
Delete the EXAMPLE scenes below once you have written your own.
"""
import math

from plumbing.core import S, T, sfx
from sketch import (Sk, esc, est_w, INK, GREY, LGREY, FAINT, RED, RED_BG, GREEN, GREEN_BG, BLUE, BLUE_BG, ORANGE,
                    YEL_BG, VIOLET, VIO_BG, CLAUDE, CLAUDE_BG)
import doodles as D
import blocks

F = None

# Listicle videos only (else delete): items are short labels for the list card and the corner tag.
blocks.LIST.update(title='', recap_title='', items=[])


def A(c, phrase, end=False, back=0.6):
    """Anchor searched from just before this scene's start (phrases repeat across a video)."""
    return T(phrase, after=c.t0 - back, end=end)


def at(c, phrase, off=0.0, lo=0.5):
    """A(c, phrase) + off, but never before `lo` s into the scene (the paper is still sliding in before that).
    Prefer this to A() for element times: an anchor word said just before the scene starts would otherwise
    animate the element while the paper is still off screen."""
    return max(A(c, phrase) + off, c.t0 + lo)


def K(c, kw, name, n=None):
    """Scene builder. n = list item number -> corner tag '#n  label' (listicles)."""
    return Sk(c, name, kw.get('prev_face', False), kw.get('next_face', False), tag=blocks.tag(n) if n else None,
              face=kw.get('face', True))


# ================================================================ EXAMPLE scenes (delete)
def s_example(c, **kw):
    k = K(c, kw, 'example')
    k.text('t', 160, 130, 'A title that lands on its word', 70, t=at(c, 'some phrase'))
    k.box('b', 220, 340, 680, 300, 'a hatched box', 64, t=at(c, 'other phrase'), stroke=BLUE, fill=BLUE_BG)
    k.arrow('a', [(920, 490), (1080, 490)], t=A(c, 'other phrase') + 0.4)
    k.logo('lg', 'claude', 1150, 400, 180, t=A(c, 'claude'))
    return k.done()


# ================================================================ storyboard
def board():
    return [
        (0.0, F, {}),
        # (T('first b-roll phrase'), s_example, {}),
        # (T('a full-canvas phrase'), s_other, {'face': False}),   # B-roll only, no face square
        # (T('back to face phrase'), F, {}),
    ]


def register():
    b = board()
    for i, (t, fn, kw) in enumerate(b):
        if fn is None:
            S(t, None)
            continue
        prev_face = i == 0 or b[i - 1][1] is None
        next_face = i + 1 >= len(b) or b[i + 1][1] is None
        S(t, fn, prev_face=prev_face, next_face=next_face, **kw)
