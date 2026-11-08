"""REFERENCE ONLY - the scenes.py of "7 practical, high ROI tasks for idle agents" (Nov 2026, user loved it).
Read it to see how the kit is used and how scenes are paced against the words. Do NOT reuse its layouts in a
new video: each video gets its own compositions.
Face segments are F (None) gaps; every time is an absolute anchor from the transcript (T / A)."""
import math, random

from plumbing.core import S, T, sfx
import doodles as D
import blocks
from sketch import (Sk, esc, est_w, INK, GREY, LGREY, FAINT, RED, RED_BG, GREEN, GREEN_BG, BLUE, BLUE_BG, ORANGE,
                    YEL_BG, VIOLET, VIO_BG, CLAUDE, CLAUDE_BG)

blocks.LIST.update(
    title='7 tasks for idle agents', recap_title='7 things my agents do for me',
    items=['Filter job listings', 'Take lectures & exams', 'Haiku runs repetitive workflows', 'Make my code faster',
           'Content research', 'Full SEO audits', 'Find messy & dead code'])


def A(c, phrase, end=False, back=0.6):
    """Anchor searched from just before this scene's start (phrases repeat across the video)."""
    return T(phrase, after=c.t0 - back, end=end)


def K(c, kw, name, n=None):
    return Sk(c, name, kw.get('prev_face', False), kw.get('next_face', False), tag=blocks.tag(n) if n else None)


# ================================================================ hook
def s_yearago(c, **kw):
    k = K(c, kw, 'yearago')
    k.text('t', 170, 140, 'Just a year ago, these were…', 70, t=A(c, 'a year ago'))
    k.underline('tu', 170, 238, 560, t=A(c, 'ago'), color=ORANGE)
    k.box('a', 230, 340, 680, 380, 'completely\nimpossible', 78, t=A(c, 'completely'), stroke=RED, fill=RED_BG,
          lstyle='white-space:pre', d=0.55)
    k.box('b', 1010, 340, 680, 380, 'too unreliable', 78, t=A(c, 'unreliable'), stroke=ORANGE, fill=YEL_BG,
          d=0.55)
    k.text('or', 960, 495, 'or', 52, t=A(c, 'or just'), align='c', w=200, color=GREY)
    k.text('m', 960, 800, "…so you wouldn't even try.", 60, t=A(c, 'meaning'), align='c', w=1400, color=GREY)
    return k.done()


# ================================================================ #1 jobs
def s_upwork(c, **kw):
    k = K(c, kw, 'upwork', 1)
    k.img('shot', 'assets/img/upwork-pub.png', 130, 170, 1100, 780, url='upwork.com/freelance-jobs/python')
    k.logo('lg', 'upwork', 1330, 200, 150, t=A(c, 'upwork'))
    k.text('fp', 1330, 390, 'a freelance platform', 54, t=A(c, 'freelance'))
    k.box('p', 1330, 500, 440, 110, 'clients post jobs', 44, t=A(c, 'people'), fill=BLUE_BG, stroke=BLUE)
    k.arrow('ar', [(1550, 625), (1550, 710)], t=A(c, 'post'), head=18)
    k.box('b', 1330, 725, 440, 110, 'you bid on them', 44, t=A(c, 'and you'), fill=GREEN_BG, stroke=GREEN)
    sfx('pop', A(c, 'upwork') + 0.15, 0.25)
    return k.done()


def s_filters(c, **kw):
    k = K(c, kw, 'filters', 1)
    # crop is 995x810; shown at 780 wide (x0.784)
    x, y, w = 130, 150, 780
    h = round(810 * w / 995) + 52
    k.img('shot', 'assets/img/upwork-search.png', x, y, w, h, url='upwork.com/nx/search/jobs')
    sc = w / 995
    k.text('t', 1010, 160, 'the built-in filters:', 64, t=A(c, 'basic filters'))
    # search box in the crop: x 5..510, y 5..45
    k.ring('r1', x + 5 * sc, y + 52 + 5 * sc, 505 * sc, 40 * sc, t=A(c, 'keyword'))
    k.text('f1', 1060, 290, '• keyword search', 52, t=A(c, 'keyword'))
    # sidebar filters: x 0..230, y 70..800
    k.rect('r2', x + 2, y + 52 + 68 * sc, 232 * sc, 735 * sc, t=A(c, 'how many hires'), stroke=RED, sw=4, rough_=1.6)
    k.text('f2', 1060, 380, '• client hires, budget…', 52, t=A(c, 'how many hires'))
    k.text('pb', 1010, 520, 'pretty basic.', 96, t=A(c, 'pretty basic'), color=ORANGE)
    k.underline('pbu', 1010, 640, 540, t=A(c, 'pretty basic') + 0.5, color=ORANGE)
    k.text('ev', 1010, 730, 'even with my favourite\nfilters applied…', 54, t=A(c, 'even after'), color=GREY, d=1.0)
    return k.done()


def s_pile(c, **kw):
    k = K(c, kw, 'pile', 1)
    D.scroller(k, 'win', 200, 160, 640, 820, c.dur)
    D.counter(k, 'n', 1000, 250, 50, 100, A(c, '50'), d=0.5, size=230)
    sfx('pop', A(c, '50') + 0.1, 0.25)
    k.text('j', 1010, 540, 'jobs to go through', 64, t=A(c, 'jobs'))
    k.text('mw', 1010, 700, 'a LOT of manual work', 70, t=A(c, 'manual'), color=INK)
    k.underline('mwu', 1010, 800, 640, t=A(c, 'work') + 0.2, color=RED)
    return k.done()


def s_funnel(c, **kw):
    k = K(c, kw, 'funnel', 1)
    t_acc, t_every = A(c, 'gave cloud'), A(c, 'every single')
    t_info, t_100, t_78, t_match = A(c, 'bunch of'), A(c, 'went through'), A(c, 'seven or eight'), A(c, 'matched')
    # left: job stack
    k.text('lj', 140, 190, '<span id="@@-c100">0</span> jobs', 60, t=t_100, anim='fade', d=0.2)
    for i in range(6):
        k.box('j%d' % i, 140, 300 + i * 84, 380, 70, '', 10, t=t_every + i * 0.08, anim='pop', sw=2.2)
    k.js("COUNT('#@@-c100',%.3f,0,100,1.0,fmtInt);" % k.L(t_100))
    k.arrow('a1', [(560, 560), (720, 560)], t=t_every + 0.6)
    # middle: browser with Claude
    k.rect('br', 740, 380, 440, 360, t=t_acc, stroke=INK, sw=3)
    k.line('brl', 740, 432, 1180, 432, t=t_acc + 0.3, sw=2.4)
    for i, col in enumerate(('#ff8787', '#ffd43b', '#69db7c')):
        k.ellipse('bd%d' % i, 770 + i * 28, 406, 15, 15, t=t_acc + 0.3, stroke=INK, sw=1.6, fill=col, fs='solid')
    k.logo('cl', 'claude', 900, 480, 120, t=t_acc + 0.4)
    k.text('cla', 960, 640, 'Claude + my browser', 40, t=t_acc + 0.6, align='c', w=440)
    sfx('pop', t_acc + 0.55, 0.25)
    # sticky: criteria
    k.box('st', 760, 120, 400, 200, "what I'm\nlooking for", 46, t=t_info, fill=YEL_BG, fs='solid', stroke=ORANGE,
          anim='pop', lstyle='white-space:pre')
    sfx('pop-low', t_info + 0.1, 0.25)
    k.arrow('a0', [(960, 325), (960, 375)], t=t_info + 0.4, head=16)
    # right: matches
    k.arrow('a2', [(1200, 560), (1360, 560)], t=t_78 - 0.4)
    k.text('lm', 1400, 190, '8 matches', 60, t=t_78, color=GREEN)
    for i in range(8):
        cx, cy = 1400 + (i % 2) * 210, 300 + (i // 2) * 120
        k.box('m%d' % i, cx, cy, 190, 96, '', 10, t=t_78 + 0.1 + i * 0.07, anim='pop', fill=GREEN_BG, stroke=GREEN,
              fs='hachure')
        k.check('mc%d' % i, cx + 95, cy + 46, 0.9, t=t_match + 0.1 + i * 0.08, snd=(i == 0))
    sfx('pop', t_78 + 0.2, 0.22)
    return k.done()


def s_howto1(c, **kw):
    k = K(c, kw, 'howto1', 1)
    t1, t2, t3, tf = A(c, 'download'), A(c, 'go over to'), A(c, 'ask it'), A(c, 'final answer')
    k.text('t', 160, 130, 'How to set it up', 70, t=A(c, 'way to implement'))
    cols = [150, 710, 1270]
    for i, (x, tt, lab) in enumerate(zip(cols, (t1, t2, t3), ('Add Claude in Chrome', 'Open the job list',
                                                               'Ask it to filter'))):
        k.box('n%d' % i, x, 250, 70, 70, str(i + 1), 40, t=tt, shape='ellipse', fill=YEL_BG, fs='solid',
              stroke=ORANGE)
        k.text('l%d' % i, x + 85, 260, lab, 40, t=tt + 0.2)
    k.img('s1', 'assets/img/claude-chrome.png', 150, 360, 500, 380, t=t1 + 0.2, url='claude.com/chrome', bar=40)
    k.text('s1b', 150, 770, '+ give it browser access', 40, t=A(c, 'give it access'), color=GREY)
    k.img('s2', 'assets/img/upwork-search.png', 710, 360, 500, 380, t=t2 + 0.2, url='upwork.com', bar=40)
    k.arrow('a1', [(660, 550), (700, 550)], t=t2, head=14)
    k.arrow('a2', [(1220, 550), (1260, 550)], t=t3, head=14)
    # chat bubble
    k.rect('bub', 1270, 360, 500, 300, t=t3, stroke=CLAUDE, fill=CLAUDE_BG, fs='solid', sw=3)
    k.text('pr', 1300, 390, 'Go through every job\non this page. Keep only\nthe ones that match\nmy filters.', 32,
           t=t3 + 0.35, d=2.4, code=True)
    for i in range(9):
        sfx('key-press', t3 + 0.45 + i * 0.27, 0.12)
    k.box('res', 1310, 700, 420, 100, 'final shortlist', 44, t=tf, anim='pop', fill=GREEN_BG, stroke=GREEN, lstyle='padding-left:56px')
    k.check('rc', 1350, 750, 0.9, t=tf + 0.25)
    return k.done()


# ================================================================ #2 lectures
def s_race(c, **kw):
    k = K(c, kw, 'race', 2)
    t_ed, t_dec, t_ai = A(c, 'education'), A(c, 'decades'), A(c, 'what ai')
    k.line('tr1', 180, 420, 1760, 420, stroke=LGREY, sw=3, dash=[14, 12])
    k.line('tr2', 180, 760, 1760, 760, stroke=LGREY, sw=3, dash=[14, 12])
    k.text('l1', 180, 310, 'AI', 54, color=VIOLET)
    k.text('l2', 180, 650, 'the education system', 54, color=INK)
    # education: a slow little box at the start
    k.box('ed', 220, 700, 220, 120, 'school', 46, t=t_ed, fill=FAINT, fs='solid', anim='pop')
    # AI marker that races to the far end
    k.box('ai', 220, 360, 160, 120, 'AI', 60, fill=VIO_BG, stroke=VIOLET, fs='hachure', color=VIOLET)
    k.js("tl.fromTo('#@@-ai',{x:0,opacity:0},{x:1330,opacity:1,duration:0.9,ease:'expo.out'},%.3f);" % k.L(t_ai))
    sfx('whoosh-high', t_ai + 0.1, 0.25)
    k.arrow('gap', [(470, 590), (1700, 590)], t=t_dec, both=True, stroke=RED, sw=3.5, d=0.6)
    k.text('dg', 1085, 515, 'decades to catch up', 56, t=t_dec + 0.3, align='c', w=900, color=RED)
    return k.done()


def s_tutor(c, **kw):
    k = K(c, kw, 'tutor', 2)
    k.text('t', 160, 130, "You're better off using AI:", 70, t=A(c, 'better off'))
    # card 1: explainer video
    t1, t2 = A(c, 'explainer'), A(c, 'personal tutor')
    k.rect('c1', 220, 290, 680, 420, t=t1, stroke=BLUE, fill=BLUE_BG, sw=3)
    k.poly('pl', [(510, 410), (510, 560), (640, 485)], t=t1 + 0.3, closed=True, stroke=INK, fill='#fff', fs='solid', sw=3)
    k.text('c1l', 560, 610, 'an explainer video', 50, t=t1 + 0.4, align='c', w=660)
    k.rect('c2', 1020, 290, 680, 420, t=t2, stroke=GREEN, fill=GREEN_BG, sw=3)
    k.box('q', 1080, 340, 380, 90, 'why does X work?', 36, fill='#fff', fs='solid', t=t2 + 0.3, anim='pop')
    k.box('a', 1260, 450, 380, 90, "here's the idea…", 36, fill='#fff', fs='solid', t=t2 + 0.6, anim='pop',
          stroke=GREEN)
    k.text('c2l', 1360, 610, 'a personal tutor', 50, t=t2 + 0.4, align='c', w=660)
    tt = A(c, 'instead')
    k.text('in', 300, 790, 'instead of', 52, t=tt, color=GREY)
    k.text('wp', 580, 790, "whatever they've planned for you", 52, t=A(c, 'whatever'), d=0.9)
    k.strike('st', 580, 825, 800, t=A(c, 'planned'))
    return k.done()


def s_lms(c, **kw):
    k = K(c, kw, 'lms', 2)
    t_acc, t_page, t_watch = A(c, 'give cloud'), A(c, 'lectures page'), A(c, 'watch')
    t_act, t_done = A(c, 'whatever actions'), A(c, 'voila')
    k.logo('cl', 'claude', 1500, 200, 150, t=t_acc)
    k.text('cla', 1575, 380, 'Claude in Chrome', 44, t=t_acc + 0.3, align='c', w=500)
    sfx('pop', t_acc + 0.15, 0.25)
    # browser window with a lecture list
    bx, by, bw, bh = 140, 160, 1160, 820
    k.rect('br', bx, by, bw, bh, t=t_page - 0.3, sw=3)
    k.line('brl', bx, by + 56, bx + bw, by + 56, t=t_page - 0.1, sw=2.4)
    k.text('bt', bx + 40, by + 80, 'My course — lectures', 50, t=t_page)
    rows = 5
    starts = [t_watch + 0.1 + i * 0.75 for i in range(rows)]
    for i in range(rows):
        y = by + 190 + i * 118
        k.text('r%d' % i, bx + 40, y, 'Lecture %d' % (i + 1), 42, t=t_page + 0.2 + i * 0.1, anim='fade')
        k.rect('pb%d' % i, bx + 300, y + 12, 620, 34, t=t_page + 0.2 + i * 0.1, sw=2, stroke=GREY)
        k.raw('<div id="@@-fill%d" style="position:absolute;left:%dpx;top:%dpx;width:612px;height:26px;'
              'background:%s;border-radius:4px;transform-origin:0 50%%"></div>' % (i, bx + 304, y + 16, GREEN_BG))
        k.js("tl.fromTo('#@@-fill%d',{scaleX:0},{scaleX:1,duration:0.7,ease:'power1.inOut'},%.3f);" % (i, k.L(starts[i])))
        k.check('ck%d' % i, bx + 990, y + 26, 0.85, t=starts[i] + 0.7, snd=(i % 2 == 0))
    k.text('w', 1340, 520, 'watches every\nlecture in full', 48, t=t_watch, d=0.8)
    k.text('ac', 1340, 680, '+ clicks what it\nneeds to', 48, t=t_act, d=0.8)
    k.box('dn', 1360, 820, 380, 130, 'done ✓', 72, t=t_done, anim='pop', stroke=GREEN, fill=GREEN_BG, color=GREEN)
    sfx('chime', t_done + 0.15, 0.22)
    return k.done()


# ================================================================ #3 haiku skills
def s_skills(c, **kw):
    k = K(c, kw, 'skills', 3)
    # a loop of repeated steps
    cx, cy, r = 520, 560, 230
    for i in range(5):
        a = -math.pi / 2 + i * 2 * math.pi / 5
        k.box('s%d' % i, cx + r * math.cos(a) - 80, cy + r * math.sin(a) - 40, 160, 80, 'step %d' % (i + 1), 34,
              stroke=GREY, color=GREY, sw=2)
    k.curve('loop', [(cx + 120, cy - 70), (cx + 40, cy - 110), (cx - 60, cy - 60), (cx - 70, cy + 40),
                     (cx + 20, cy + 90), (cx + 100, cy + 40)], stroke=GREY, sw=2.4)
    k.text('ag', cx, cy - 30, 'again &\nagain', 40, align='c', w=300, color=GREY)
    k.text('rw', 230, 210, 'a repetitive workflow', 50, color=INK)
    k.arrow('ar', [(860, 560), (1130, 560)], t=A(c, 'turning'), sw=3.5, d=0.5)
    # SKILL.md file
    fx, fy = 1200, 330
    k.poly('file', [(fx, fy), (fx + 330, fy), (fx + 420, fy + 90), (fx + 420, fy + 470), (fx, fy + 470)], closed=True,
           t=A(c, 'skills', back=0.4) - 0.6, stroke=GREEN, fill=GREEN_BG, sw=3.5, d=0.5)
    k.poly('fold', [(fx + 330, fy), (fx + 330, fy + 90), (fx + 420, fy + 90)], t=A(c, 'skills', back=0.4) - 0.2,
           stroke=GREEN, sw=3)
    k.text('fn', fx + 210, fy + 180, 'SKILL.md', 64, t=A(c, 'skills', back=0.4) - 0.1, align='c', w=420, code=True)
    k.text('fs', fx + 210, fy + 300, 'a skill', 50, t=A(c, 'skills', back=0.4) + 0.1, align='c', w=420, color=GREEN)
    sfx('pop', A(c, 'skills', back=0.4), 0.25)
    return k.done()


PIPE = [('record', None), ('compress', 'compress'), ('upload to R2', 'upload'), ('captions', 'captions'),
        ('summaries', 'summaries'), ('timestamps', 'timestamps'), ('chapters', 'chapters'),
        ('set up website', 'set everything'), ('grab the link', 'grab'), ('send to client', 'send it')]


def s_pipeline(c, **kw):
    k = K(c, kw, 'pipeline', 3)
    k.text('t', 160, 130, 'Custom Loom outreach, by hand:', 64, t=c.t0 + 0.2)
    bw, bh = 290, 130
    pos = []
    for i in range(10):
        row, col = divmod(i, 5)
        x = 140 + (col if row == 0 else 4 - col) * 340
        y = 330 if row == 0 else 680
        pos.append((x, y))
    for i, (lab, ph) in enumerate(PIPE):
        x, y = pos[i]
        t = None if ph is None else A(c, ph)
        col = (BLUE, BLUE_BG) if i < 5 else (VIOLET, VIO_BG)
        k.box('b%d' % i, x, y, bw, bh, lab, 42, t=t, stroke=col[0], fill=col[1], d=0.4)
        if i:
            px, py = pos[i - 1]
            if py == y:
                xs = (px + bw + 6, x - 6) if x > px else (px - 6, x + bw + 6)
                k.arrow('a%d' % i, [(xs[0], y + bh / 2), (xs[1], y + bh / 2)], t=t - 0.25, head=14, d=0.25)
            else:
                k.arrow('a%d' % i, [(px + bw / 2, py + bh + 6), (x + bw / 2, y - 6)], t=t - 0.25, head=14, d=0.3)
        if t:
            sfx('pop-low', t + 0.05, 0.16)
    k.logo('cf', 'cloudflare', pos[2][0] + 95, pos[2][1] - 120, 100, t=A(c, 'cloudflare'))
    k.text('cam', pos[0][0] + bw / 2, pos[0][1] + bh + 20, '(this video)', 34, align='c', w=300, color=GREY)
    return k.done()


def s_slash(c, **kw):
    k = K(c, kw, 'slash', 3)
    k.text('t', 160, 130, 'That\'s a lot of manual steps.', 64, t=A(c, 'manual steps') - 0.3)
    t_hand, t_cli, t_cmd, t_sl = A(c, 'by hand'), A(c, 'script'), A(c, 'type the commands'), A(c, 'slash')
    k.box('h', 160, 300, 620, 130, 'all 10 steps by hand', 46, t=t_hand - 0.4)
    k.cross('hx', 820, 365, 1.1, t=t_hand + 0.2)
    k.rect('term', 160, 480, 620, 400, t=t_cli, stroke=INK, fill='#f1f3f5', fs='solid', sw=3)
    k.text('cmd', 190, 510, '$ compress rec.mp4\n$ upload out.mp4 r2:\n$ make-captions …\n$ make-summary …\n$ …',
           32, t=t_cli + 0.3, d=1.6, code=True, color=INK)
    k.cross('tx', 820, 680, 1.1, t=t_cmd + 0.3)
    # the one command
    k.rect('cc', 960, 420, 800, 260, t=t_sl - 0.8, stroke=CLAUDE, sw=3.5)
    k.text('ccl', 990, 440, 'Claude Code', 36, t=t_sl - 0.6, color=CLAUDE)
    k.text('sl', 1000, 520, '> /publish-loom', 64, t=t_sl, d=1.0, code=True)
    for i in range(7):
        sfx('key-press', t_sl + 0.1 + i * 0.15, 0.14)
    return k.done()


def s_haiku(c, **kw):
    k = K(c, kw, 'haiku', 3)
    t_h, t_call, t_all = A(c, 'haiku'), A(c, 'call the skill'), A(c, 'all of these')
    k.box('cmd', 760, 150, 400, 90, '/publish-loom', 44, code=True, stroke=CLAUDE)
    k.arrow('a1', [(960, 250), (960, 320)], t=t_h - 0.3, head=16)
    k.box('hk', 760, 330, 400, 140, 'Haiku', 70, t=t_h, stroke=GREEN, fill=GREEN_BG, anim='pop')
    k.text('cs', 1200, 370, 'calls the skill', 46, t=t_call)
    sfx('pop', t_h + 0.1, 0.25)
    for i, (lab, _) in enumerate(PIPE):
        row, col = divmod(i, 5)
        x, y = 150 + col * 330, 600 + row * 170
        k.box('p%d' % i, x, y, 290, 110, lab, 38, t=t_call + 0.4 + i * 0.05, anim='pop', stroke=GREY, sw=2)
        k.check('pc%d' % i, x + 262, y + 22, 0.75, t=t_all + i * 0.13, snd=(i % 3 == 0))
    return k.done()


# ================================================================ #4 faster
def s_prompt(c, **kw):
    k = K(c, kw, 'prompt', 4)
    t_op, t_each, t_fast = A(c, 'opus'), A(c, 'each'), A(c, 'make it faster')
    t_no = A(c, "i didn't give")
    k.text('sp', 140, 190, 'my side projects', 48, color=GREY)
    for i, lab in enumerate(('Reddit scraper', 'side project', 'side project')):
        y = 290 + i * 190
        k.box('p%d' % i, 140, y, 420, 130, lab, 46, stroke=INK, sw=2.6)
        k.text('sl%d' % i, 150, y + 142, 'works, but slow…', 34, color=RED)
    k.box('op', 760, 450, 340, 150, 'Opus 5.5', 62, t=t_op, stroke=VIOLET, fill=VIO_BG, anim='pop')
    sfx('pop', t_op + 0.1, 0.25)
    for i in range(3):
        k.arrow('a%d' % i, [(750, 525), (580, 360 + i * 190)], t=t_each + i * 0.15, head=16, d=0.3)
    k.box('bub', 720, 190, 460, 110, 'make it faster.', 44, t=t_fast, code=True, stroke=CLAUDE, fill=CLAUDE_BG,
          fs='solid', anim='pop')
    sfx('key-press', t_fast + 0.1, 0.15)
    k.text('no', 1240, 330, 'what I did NOT give it:', 48, t=t_no, color=GREY)
    for i, (lab, ph) in enumerate((('creative direction', 'creative'), ('engineering direction', 'engineering'),
                                   ('a specific approach', 'specific'))):
        tt = A(c, ph)
        y = 450 + i * 130
        k.text('n%d' % i, 1320, y, lab, 50, t=tt)
        k.cross('x%d' % i, 1270, y + 32, 0.8, t=tt + 0.1)
    return k.done()


def s_fast(c, **kw):
    k = K(c, kw, 'fast', 4)
    k.text('t', 160, 130, 'It handled everything.', 72, t=A(c, 'handled'))
    k.text('b1', 160, 300, 'before', 44, color=GREY)
    k.rect('bar1', 340, 290, 1300, 70, stroke=RED, fill=RED_BG, sw=3)
    k.text('b2', 160, 420, 'after', 44, t=A(c, 'handled') + 0.2, color=GREY)
    k.rect('bar2', 340, 410, 300, 70, t=A(c, 'handled') + 0.3, stroke=GREEN, fill=GREEN_BG, sw=3, d=0.4)
    k.text('ill', 1640, 500, '(illustrative)', 30, align='r', w=400, color=LGREY)
    # target
    tp = A(c, 'point it')
    D.target(k, 'tg', 1400, 790, t=tp)
    k.box('op', 300, 730, 320, 130, 'Opus', 60, t=tp, stroke=VIOLET, fill=VIO_BG, anim='pop')
    k.arrow('ta', [(640, 795), (1380, 790)], t=A(c, 'problem') - 0.1, sw=3.5, d=0.35)
    k.text('pr', 1550, 700, 'the problem', 46, t=A(c, 'problem'), color=RED)
    k.text('go', 960, 940, '…and let it do its thing.', 54, t=A(c, 'throw'), align='c', w=1200, d=0.9)
    sfx('pop-low', A(c, 'problem') + 0.25, 0.22)
    return k.done()


# ================================================================ #5 research
def s_research1(c, **kw):
    k = K(c, kw, 'research1', 5)
    k.text('t', 960, 140, 'Two ways Opus helps me', 70, t=A(c, 'two ways'), align='c', w=1400)
    k.box('op', 760, 300, 400, 130, 'Opus', 64, t=A(c, 'opus', back=0.2), stroke=VIOLET, fill=VIO_BG, anim='pop')
    t1 = A(c, 'one is')
    k.arrow('a1', [(800, 440), (560, 600)], t=t1 - 0.2)
    k.arrow('a2', [(1120, 440), (1360, 600)], t=t1 - 0.2)
    k.box('b1', 220, 620, 680, 170, '① find new topics to learn', 44, t=A(c, 'one is'), stroke=BLUE, fill=BLUE_BG)
    k.box('b2', 1020, 620, 680, 170, '② …', 50, t=t1, stroke=LGREY, color=LGREY, sw=2)
    return k.done()


def s_research2(c, **kw):
    k = K(c, kw, 'research2', 5)
    k.text('t', 160, 130, '① research + learning resources', 64, t=c.t0 + 0.2)
    k.box('op', 160, 470, 300, 130, 'Opus', 60, stroke=VIOLET, fill=VIO_BG)
    tr = A(c, 'research')
    k.arrow('a', [(480, 535), (640, 535)], t=tr)
    for i, lab in enumerate(('docs', 'papers', 'talks & videos', 'blog posts')):
        k.box('r%d' % i, 680, 290 + i * 135, 420, 110, lab, 46, t=A(c, 'finding') + i * 0.2, anim='pop',
              stroke=BLUE, fill=BLUE_BG if i % 2 == 0 else '#fff', fs='hachure')
    sfx('pop', A(c, 'finding') + 0.1, 0.2)
    # lightbulb
    ti = A(c, 'have an idea')
    D.lightbulb(k, 'bulb', 1460, 430, t=ti)
    k.text('k', 1460, 680, "so I actually know\nwhat I'm talking about", 46, t=ti + 0.2, align='c', w=620)
    return k.done()


def s_scripts(c, **kw):
    k = K(c, kw, 'scripts', 5)
    k.text('t', 160, 130, '② structuring video scripts', 64, t=A(c, 'structuring'))
    rng = random.Random(7)
    pts = [(300 + rng.randint(-150, 330), 380 + rng.randint(-60, 420)) for _ in range(16)]
    ty = A(c, 'yapping')
    k.curve('mess', pts, t=ty - 0.4, d=1.3, stroke=GREY, sw=3, rough_=1.6)
    k.text('ml', 160, 900, 'my early videos: yapping', 46, t=ty, color=GREY)
    k.arrow('ar', [(900, 600), (1060, 600)], t=A(c, 'train of'), sw=3.5)
    # outline (from this video's actual outline.md)
    tt = A(c, 'train of') + 0.3
    k.rect('note', 1100, 250, 640, 700, t=tt - 0.2, stroke=ORANGE, fill=YEL_BG, fs='solid', sw=3, d=0.4)
    k.text('oh', 1140, 280, 'outline.md', 44, t=tt, code=True)
    lines = ['# Hook', '1. jobs', '2. lectures & exams', '3. Haiku + skills', '[like / subscribe]', '4. faster code',
             '5. … 7.']
    for i, ln in enumerate(lines):
        k.text('o%d' % i, 1150, 370 + i * 76, ln, 40, t=tt + 0.15 + i * 0.12, anim='fade',
               color=INK if not ln.startswith('[') else GREY)
    k.text('tl', 1420, 965, 'a real train of thought', 40, t=tt + 0.4, align='c', w=640, color=ORANGE)
    return k.done()


# ================================================================ #6 SEO
def s_seo(c, **kw):
    k = K(c, kw, 'seo', 6)
    t_sk, t_12 = A(c, 'skill for seo'), A(c, '12')
    k.box('sk', 780, 540, 360, 140, 'SEO skill', 60, t=t_sk, stroke=GREEN, fill=GREEN_BG, anim='pop')
    k.text('dl', 960, 700, '(downloadable)', 34, t=A(c, 'download'), align='c', w=500, color=GREY)
    sfx('pop', t_sk + 0.1, 0.25)
    k.text('sa', 960, 120, '12–15 sub-agents', 64, t=t_12, align='c', w=1000, color=VIOLET)
    n, rx, ry = 14, 560, 300
    cx, cy = 960, 610
    for i in range(n):
        a = -math.pi / 2 + i * 2 * math.pi / n
        x, y = cx + rx * math.cos(a), cy + ry * math.sin(a)
        k.ellipse('ag%d' % i, x, y, 70, 70, t=t_12 + 0.1 + i * 0.06, stroke=VIOLET, fill=VIO_BG, fs='solid', sw=2.4,
                  d=0.25)
    sfx('sparkle', t_12 + 0.3, 0.15)
    labels = [('content', 'content', 2), ('backlinks', 'backlinks', 4), ('external SEO', 'external', 5),
              ('internal SEO', 'internal', 9), ('technical', 'technical', 10), ('page speed', 'page speed', 12)]
    for j, (lab, ph, i) in enumerate(labels):
        a = -math.pi / 2 + i * 2 * math.pi / n
        x, y = cx + rx * math.cos(a), cy + ry * math.sin(a)
        right = math.cos(a) >= 0
        tx = x + 55 if right else x - 55
        k.text('lb%d' % j, tx, y - 28, lab, 44, t=A(c, ph), align='l' if right else 'r', w=None if right else 500)
    k.text('ev', 960, 420, 'everything.', 60, t=A(c, 'everything'), align='c', w=600, color=ORANGE)
    return k.done()


def s_score(c, **kw):
    k = K(c, kw, 'score', 6)
    ts, ti = A(c, 'score'), A(c, 'tells you') + 0.2
    k.rect('doc', 220, 200, 640, 780, stroke=INK, sw=3)
    k.text('dt', 260, 230, 'SEO audit report', 50)
    D.gauge(k, 'g', 540, 600, 220, t=ts, label='score / 100')
    k.text('it', 980, 230, 'what to improve:', 56, t=ti - 0.3)
    for i in range(4):
        y = 360 + i * 130
        k.rect('cb%d' % i, 990, y, 54, 54, t=ti + i * 0.12, sw=2.6, d=0.2)
        k.line('cl%d' % i, 1080, y + 28, 1080 + 380 + (i * 97) % 220, y + 28, t=ti + 0.1 + i * 0.12, stroke=LGREY, sw=6,
               d=0.3)
    return k.done()


def s_roi(c, **kw):
    k = K(c, kw, 'roi', 6)
    t20, tsame, top, t10, thr = A(c, '20'), A(c, 'exact same'), A(c, 'with opus'), A(c, 'taking like'), A(c, 'couple of hours')
    k.text('t', 960, 110, 'The ROI', 80, t=c.t0 + 0.2, align='c', w=800)
    k.rect('l', 160, 260, 760, 640, stroke=RED, sw=3)
    k.text('lt', 540, 300, 'an SEO agency', 60, align='c', w=700)
    k.text('usdt', 540, 450, '$<span id="@@-usd">0</span>', 150, t=t20 - 0.1, align='c', w=740, color=RED, anim='fade', d=0.15)
    k.js("COUNT('#@@-usd',%.3f,0,20000,0.9,fmtInt,'power3.out');" % k.L(t20 - 0.1))
    sfx('impact-bass-1', t20 + 0.7, 0.3)
    k.text('ls', 540, 720, 'for the same info\n+ implementation', 46, t=tsame, align='c', w=700, color=GREY, d=0.8)
    k.rect('r', 1000, 260, 760, 640, t=top, stroke=GREEN, sw=3)
    k.text('rt', 1380, 300, 'Opus + an SEO skill', 60, t=top, align='c', w=740)
    k.text('r1', 1380, 450, '10–20 min', 110, t=t10, align='c', w=740, color=GREEN)
    k.text('r1s', 1380, 590, 'to run the audit', 44, t=t10 + 0.4, align='c', w=740, color=GREY)
    k.text('r2', 1380, 690, '+ a couple of hours', 60, t=thr, align='c', w=740, color=GREEN)
    k.text('r2s', 1380, 780, 'to make the fixes', 44, t=thr + 0.4, align='c', w=740, color=GREY)
    return k.done()


# ================================================================ #7 code smells
def s_smells(c, **kw):
    k = K(c, kw, 'smells', 7)
    lines = D.code_file(k, 'cf', 160, 180, 720, 800, title='your codebase')
    items = [('messy code', 'messy', 1), ('repeated logic', 'repeated', 3), ('code smells', 'smells', 5),
             ('broken flows', 'broken', 8), ('dead code', 'dead', 10)]
    for i, (lab, ph, li) in enumerate(items):
        tt = A(c, ph)
        y = 230 + i * 150
        k.rect('b%d' % i, 1040, y, 60, 60, t=tt - 0.15, sw=2.6, d=0.2)
        k.text('t%d' % i, 1130, y - 2, lab, 58, t=tt)
        lx, ly, lw = lines[li]
        k.ring('rg%d' % i, lx, ly - 14, lw, 28, t=tt + 0.1, color=RED, sw=3.5, d=0.35)
        k.check('c%d' % i, 1072, y + 28, 0.8, t=tt + 0.3, color=RED, snd=(i % 2 == 0))
    return k.done()


def s_usage(c, **kw):
    k = K(c, kw, 'usage', 7)
    k.text('t', 160, 130, 'Free usage lying around?', 70, t=A(c, 'free usage'))
    x, y, sc, bar = 290, 290, 2.0, 52
    k.img('shot', 'assets/img/claude-usage.png', x, y, int(670 * sc), int(200 * sc) + bar, url='claude.ai/settings/usage')
    oy = y + bar
    k.ring('rs', x + 8 * sc, oy + 168 * sc, 128 * sc, 20 * sc, t=A(c, 'reset'), color=RED)
    k.text('rl', x, oy + 200 * sc + 60, 'resets on its own', 46, t=A(c, 'reset') + 0.3, color=RED)
    # the unused part of the weekly bar (8% used)
    bx0, bx1, by = x + 236 * sc, x + 573 * sc, oy + 166 * sc
    k.mark('un', bx0, by - 18, bx1 - bx0, 36, t=A(c, 'spend it'), color=YEL_BG)
    k.arrow('ua', [(1240, oy + 200 * sc + 50), (1280, by + 28)], t=A(c, 'spend it'), d=0.35)
    k.text('ul', 960, oy + 200 * sc + 60, '92% of my week, unused', 52, t=A(c, 'spend it') + 0.2, color=ORANGE)
    k.text('sp', 960, oy + 200 * sc + 140, '→ spend it on this', 52, t=A(c, 'spend it on this'), color=INK)
    return k.done()


def s_prompt7(c, **kw):
    k = K(c, kw, 'prompt7', 7)
    th = A(c, 'hey')
    k.text('l', 200, 230, 'the prompt:', 52, color=GREY)
    D.prompt(k, 'pr', 190, 330, 1540, 340,
             '> hey, go find dead code, bugs,\n  issues and broken logic in\n  this entire codebase', th,
             until=A(c, 'code base', end=True), size=64)
    return k.done()


def s_found(c, **kw):
    k = K(c, kw, 'found', 7)
    tf = A(c, 'find so many')
    a = D.code_file(k, 'f1', 160, 200, 560, 760, title='scraper.py')
    b = D.code_file(k, 'f2', 780, 200, 560, 760, title='api.py')
    picks = [(a, 1), (a, 4), (b, 2), (a, 8), (b, 6), (b, 9)]
    for i, (ls, li) in enumerate(picks):
        lx, ly, lw = ls[li]
        k.ring('r%d' % i, lx, ly - 14, lw, 28, t=tf + 0.2 + i * 0.18, sw=3.5, d=0.3)
    sfx('pop-low', tf + 0.3, 0.2)
    k.text('s', 1420, 260, 'so many issues', 58, t=tf, color=RED)
    k.text('o', 1420, 350, 'in its own code', 48, t=A(c, 'own code'))
    tn = A(c, 'neither')
    k.text('y', 1420, 520, 'missed by you', 46, t=tn)
    k.cross('yx', 1385, 548, 0.7, t=tn + 0.1)
    k.text('m', 1420, 620, 'missed by Opus', 46, t=A(c, 'nor'))
    k.cross('mx', 1385, 648, 0.7, t=A(c, 'nor') + 0.1)
    k.text('w', 1420, 780, '…when it was\nfirst written', 46, t=A(c, 'nor') + 0.6, color=GREY, d=0.7)
    return k.done()


def s_weekly(c, **kw):
    k = K(c, kw, 'weekly', 7)
    k.text('t', 160, 130, 'Run it every week or two', 70, t=A(c, 'run this'))
    tw = A(c, 'every week')
    for i in range(6):
        x = 160 + i * 270
        k.box('w%d' % i, x, 260, 220, 120, 'week %d' % (i + 1), 42, stroke=GREY, sw=2.2)
        k.check('c%d' % i, x + 190, 270, 0.8, t=tw + 0.15 + i * 0.25, snd=(i % 2 == 0))
    # health line
    k.line('ax', 260, 960, 1220, 960, t=A(c, 'roi') - 0.5, stroke=INK, sw=3)
    k.line('ay', 260, 960, 260, 470, t=A(c, 'roi') - 0.5, stroke=INK, sw=3)
    k.text('yl', 290, 470, 'codebase health', 40, t=A(c, 'roi') - 0.3, color=GREY)
    k.curve('hl', [(270, 920), (470, 885), (670, 810), (870, 710), (1060, 600), (1220, 500)], t=A(c, 'roi'), d=1.0,
            stroke=GREEN, sw=5)
    k.text('roi', 1290, 470, 'ROI: very high', 54, t=A(c, 'very high'), color=GREEN)
    k.text('st', 1290, 590, 'more stable', 48, t=A(c, 'stable'))
    k.text('mt', 1290, 680, 'more maintainable', 48, t=A(c, 'maintainable'))
    return k.done()


# ================================================================ storyboard
F = None


def board():
    """(start, fn, kwargs). fn None = face only."""
    ch = lambda n, **kw: (blocks.s_listcard, dict(n=n, **kw))
    return [
        (0.0, F, {}),
        (T('and these are things'), s_yearago, {}),
        (T('point of this video'), F, {}),
        (T("let's go"), *ch(1, sub='Claude finds the jobs worth\napplying to', head_at=T("let's go") + 0.15,
                              item_at=T('have claude look'), sub_at=T('filter out'))),
        (T('now i\'m doing this'), s_upwork, {}),
        (T('and they do provide'), s_filters, {}),
        (T('have like 50') + 0.0, s_pile, {}),
        (T('what i did', after=50), s_funnel, {}),
        (T('and then i can just skip'), F, {}),
        (T('the way to implement'), s_howto1, {}),
        (T('number two'), F, {}),
        (T('but have claude take'), *ch(2, sub='have Claude take lectures\nand exams for you',
                                        item_at=T('take lectures'), sub_at=T('take lectures'))),
        (T('the reality is'), s_race, {}),
        (T("and if you're still"), F, {}),
        (T("you're better off"), s_tutor, {}),
        (T('and this is especially useful'), F, {}),
        (T('now this is extremely easy'), s_lms, {}),
        (T('thirdly'), *ch(3, sub='Haiku: a small but capable model,\non repetitive tasks', item_at=T('have haiku'),
                           sub_at=T('really small'))),
        (T('and the way you achieve'), s_skills, {}),
        (T('now one of the most'), F, {}),
        (T('compress') - 0.9, s_pipeline, {}),
        (T("there's a lot of manual"), s_slash, {}),
        (T('and then haiku can'), s_haiku, {}),
        (T('so that all i do'), F, {}),
        (T('number four'), *ch(4, sub='point Opus at your slow code', item_at=T('make your code run'),
                               sub_at=T('number four') + 0.5)),
        (T("i've got a ton"), F, {}),
        (T('i then had opus'), s_prompt, {}),
        (T('no i literally just said'), F, {}),
        (T('it handled everything'), s_fast, {}),
        (T('number five'), *ch(5, sub='finding topics + structuring\nvideo scripts', item_at=T('number five') + 0.3,
                               sub_at=T('number five') + 0.6)),
        (T('this is one thing'), F, {}),
        (T("there's basically two"), s_research1, {}),
        (T('because i don\'t want'), F, {}),
        (T('so doing research'), s_research2, {}),
        (T('and also in structuring'), s_scripts, {}),
        (T('this helps me manage'), F, {}),
        (T('number six'), *ch(6, sub='comprehensive SEO audits\nof your site', item_at=T('seo audits'),
                              sub_at=T('comprehensive seo'))),
        (T('so there is a skill'), s_seo, {}),
        (T('it does a very comprehensive seo audit and'), s_score, {}),
        (T("now here's the thing"), F, {}),
        (T('there are seo agencies'), s_roi, {}),
        (T('finally'), *ch(7, sub='let Opus hunt through\nyour whole codebase', item_at=T('number seven'),
                           sub_at=T('go through your entire code'))),
        (T('find messy code') - 0.05, s_smells, {}),
        (T("you'd be surprised how useful this prompt"), F, {}),
        (T('especially if you have some free'), s_usage, {}),
        (T('if you literally just tell'), s_prompt7, {}),
        (T("it's going to obviously"), F, {}),
        (T('this is where you should be spending'), s_found, {}),
        (T('and if you literally just run'), s_weekly, {}),
        (T('anyway that is'), blocks.s_listcard, dict(recap=True)),
        (T('yours are going'), F, {}),
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
