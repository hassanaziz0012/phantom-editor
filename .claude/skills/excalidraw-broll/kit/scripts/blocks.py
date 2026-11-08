"""Scene blocks for listicle videos ("7 things...", "5 mistakes..."): a chapter card per item and a recap.
Optional: use them only when the video really is a numbered list. Configure LIST from scenes.py:

    blocks.LIST.update(title='7 tasks for idle agents', recap_title='7 things my agents do for me',
                       items=['Filter job listings', 'Take lectures & exams', ...])

Then in board():  (T('number two'), blocks.s_listcard, dict(n=2, sub='...', item_at=T(...), sub_at=T(...)))
and the recap:    (T('so those are'), blocks.s_listcard, dict(recap=True))
Change the right-hand side (big number + sub line) per video if you want the cards to feel different; e.g. swap
the circled number for a doodle of the item, or put the list on the right.
"""
from plumbing.core import sfx
from sketch import Sk, INK, GREY, LGREY, FAINT, RED, RED_BG, GREEN, GREEN_BG, BLUE, BLUE_BG, ORANGE, YEL_BG, VIOLET, VIO_BG

LIST = dict(title='', recap_title='', items=[])
COLS = [(BLUE, BLUE_BG), (VIOLET, VIO_BG), (GREEN, GREEN_BG), (ORANGE, YEL_BG), (RED, RED_BG)]


def col(n):
    return COLS[(n - 1) % len(COLS)]


def tag(n):
    """tag=(n, label) for Sk(..., tag=blocks.tag(n)) on the other scenes of item n."""
    return (n, LIST['items'][n - 1]) if LIST['items'] else None


def s_listcard(c, n=1, sub='', sub_at=None, item_at=None, head_at=None, recap=False, check_x=975, **kw):
    items = LIST['items']
    k = Sk(c, 'list%d' % (0 if recap else n), kw.get('prev_face', False), kw.get('next_face', False))
    x0, y0 = 150, 250
    step = min(98, 700 / max(1, len(items) - 1))
    title = LIST['recap_title'] if recap else LIST['title']
    k.text('ttl', x0, 140, title, 46, t=head_at, color=INK if recap else GREY)
    k.underline('ttlu', x0, 205, min(620, len(title) * 23), t=head_at and head_at + 0.4,
                color=ORANGE if recap else LGREY)
    for i, name in enumerate(items):
        y = y0 + i * step
        num = i + 1
        cc, bg = col(num)
        if recap or num < n:
            k.box('n%d' % num, x0, y, 70, 70, str(num), 38, stroke=cc if recap else GREY, shape='ellipse',
                  color=INK if recap else GREY, sw=2.2)
            k.text('i%d' % num, x0 + 95, y + 6, name, 44, color=INK if recap else GREY)
            k.check('ck%d' % num, check_x, y + 30, 1.0 if recap else 0.9,
                    t=(c.t0 + 0.55 + i * 0.22) if recap else None, snd=(i % 2 == 0))
        elif num == n:
            k.mark('mk', x0 + 80, y + 4, len(name) * 23 + 40, 62, t=item_at, color=bg)
            k.box('n%d' % num, x0, y, 70, 70, str(num), 40, stroke=cc, fill=bg, fs='solid', shape='ellipse',
                  t=c.t0 + 0.25, sw=3)
            k.text('i%d' % num, x0 + 95, y + 4, name, 46, t=item_at)
        else:
            k.box('n%d' % num, x0, y, 70, 70, str(num), 38, stroke=LGREY, shape='ellipse', color=LGREY, sw=2)
            k.line('ph%d' % num, x0 + 100, y + 48, x0 + 100 + 260 + (i * 53) % 140, y + 48, stroke=FAINT, sw=3,
                   rough_=1.5)
    if recap:
        sfx('sparkle', c.t0 + 0.6, 0.25)
        return k.done()
    cc, bg = col(n)
    k.ellipse('big', 1390, 470, 560, 540, t=c.t0 + 0.15, d=0.6, stroke=cc, fill=bg, sw=4, gap=11, fw=2.6)
    k.text('num', 1390, 200, str(n), 400, t=c.t0 + 0.35, align='c', w=600, anim='pop', color=INK)
    sfx('pop', c.t0 + 0.45, 0.3)
    if sub:
        k.text('sub', 1390, 800, sub, 46, t=sub_at or c.t0 + 0.9, align='c', w=860, color=INK)
    return k.done()
