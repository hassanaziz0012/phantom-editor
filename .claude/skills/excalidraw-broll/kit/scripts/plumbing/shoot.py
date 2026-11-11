#!/usr/bin/env python3
"""Screenshot web pages with Playwright: <name>.png (viewport) and <name>-tall.png (top of the full page).

Usage:
  python3 shoot.py OUT_DIR name=https://example.com other=https://...
  python3 shoot.py OUT_DIR targets.json          # {"name": "url"} or {"name": {"url": ..., "texts": [...]}}
Options (env): VIEW=1600x1000  SCALE=1.25  TALL=3200 (css px)  WAIT=4500 (ms)
Images are VIEW * SCALE pixels (default 2000x1250). Cookie banners are clicked away when possible.

targets.json entries can do everything a scene usually needs, cropped AT CAPTURE so no ffmpeg crop is needed
and the measured boxes stay exact (write straight into assets/img):
  {"filters": {"url": "https://site/", "view": "1600x2200",      # viewport (css px), optional
               "click": ["Show likely scams", "css=#accept"],    # clicked in order, failures only warn
               "scroll": "Remote region",                        # scroll so this is near the top
               "element": "css=aside",                           # shoot only this element (+ "pad": 24 css px)
               "clip": [x, y, w, h],                             # or an explicit page-css-px crop
               "texts": ["Possible scam", "Paid", "css=.price"]}}  # measure these
  Selectors: "css=..." is CSS, anything else is the first element containing that text.
Every target writes <name>.png and <name>.json: {"w", "h", "boxes": {text: [x, y, w, h] in image px or null}}.
In scenes, k.shot() places the image and reads that json, so rings and marks land on the measured boxes.
Plain "name=url" targets keep the old behaviour (<name>.png viewport + <name>-tall.png).
"""
import asyncio, json, os, sys

from playwright.async_api import async_playwright

VW, VH = (int(x) for x in os.environ.get('VIEW', '1600x1000').split('x'))
SCALE = float(os.environ.get('SCALE', '1.25'))
TALL = int(os.environ.get('TALL', '3200'))
WAIT = int(os.environ.get('WAIT', '4500'))
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
CONSENT = ['Accept all', 'Accept All', 'Accept', 'I agree', 'Agree', 'Allow all', 'Got it', 'OK', 'Accept Cookies']
BANNERS = ("()=>document.querySelectorAll('[id*=onetrust],[class*=cookie-],[id*=cookie],[id*=truste],"
           ".truste_overlay,.truste_box_overlay,[class*=CookieBanner]').forEach(e=>e.remove())")


def targets(args):
    """name=url args or one JSON file -> {name: {"url": ..., "texts": [...]}}"""
    if len(args) == 1 and args[0].endswith('.json'):
        raw = json.load(open(args[0]))
    else:
        raw = dict(a.split('=', 1) for a in args)
    return {n: (v if isinstance(v, dict) else {'url': v}) for n, v in raw.items()}


async def open_page(ctx, url):
    """New page at url, waited, consent clicked, banners removed, scrolled to top."""
    p = await ctx.new_page()
    await p.goto(url, wait_until='domcontentloaded', timeout=30000)
    await p.wait_for_timeout(WAIT)
    for t in CONSENT:
        el = p.get_by_role('button', name=t, exact=True).first
        try:
            if await el.is_visible():
                await el.click(timeout=2000)
                await p.wait_for_timeout(800)
                break
        except Exception:
            pass
    await p.evaluate(BANNERS)
    await p.evaluate('window.scrollTo(0,0)')
    await p.wait_for_timeout(300)
    return p


async def browser_context(pw):
    b = await pw.chromium.launch()
    ctx = await b.new_context(viewport={'width': VW, 'height': VH}, device_scale_factor=SCALE, locale='en-US', user_agent=UA)
    return b, ctx


def loc(p, sel):
    """'css=.foo' -> CSS selector; anything else -> first element containing that text."""
    return (p.locator(sel[4:]) if sel.startswith('css=') else p.get_by_text(sel, exact=False)).first


async def page_box(p, sel):
    """Element box in page CSS px (x, y, w, h), or None."""
    try:
        bb = await loc(p, sel).bounding_box(timeout=4000)
    except Exception:
        return None
    if not bb:
        return None
    sx, sy = await p.evaluate('[scrollX, scrollY]')
    return [bb['x'] + sx, bb['y'] + sy, bb['width'], bb['height']]


async def shoot_one(b, out, n, t):
    """One target. Plain url -> old behaviour (<n>.png viewport + <n>-tall.png). With any of view / click /
    scroll / element / clip -> just <n>.png, cropped at capture time, plus <n>.json with measured boxes."""
    vw, vh = (int(x) for x in t.get('view', '%dx%d' % (VW, VH)).split('x'))
    ctx = await b.new_context(viewport={'width': vw, 'height': vh}, device_scale_factor=SCALE, locale='en-US',
                              user_agent=UA)
    try:
        p = await open_page(ctx, t['url'])
        for c in t.get('click', []):
            try:
                await loc(p, c).click(timeout=4000)
                await p.wait_for_timeout(int(t.get('click_wait', 900)))
            except Exception as e:
                print('warn %s: click %r failed (%s)' % (n, c, str(e).split('\n')[0][:90]))
        special = any(k in t for k in ('view', 'click', 'scroll', 'element', 'clip'))
        if not special:
            await p.screenshot(path=f'{out}/{n}.png')
            h = await p.evaluate('document.documentElement.scrollHeight')
            await p.screenshot(path=f'{out}/{n}-tall.png', full_page=True,
                               clip={'x': 0, 'y': 0, 'width': vw, 'height': min(h, TALL)})
            clip = [0, 0, vw, vh]
        else:
            if 'scroll' in t:
                bb = await page_box(p, t['scroll'])
                if bb:
                    await p.evaluate('y=>window.scrollTo(0,y)', max(0, bb[1] - int(t.get('scroll_margin', 40))))
                    await p.wait_for_timeout(600)
                else:
                    print('warn %s: scroll target %r not found' % (n, t['scroll']))
            sy = await p.evaluate('scrollY')
            pad = int(t.get('pad', 24))
            if 'element' in t:
                bb = await page_box(p, t['element'])
                if not bb:
                    raise RuntimeError('element %r not found' % t['element'])
                clip = [max(0, bb[0] - pad), max(0, bb[1] - pad), bb[2] + 2 * pad, bb[3] + 2 * pad]
            elif 'clip' in t:
                clip = list(t['clip'])
            else:
                clip = [0, sy, vw, vh]
            await p.screenshot(path=f'{out}/{n}.png', full_page=True,
                               clip=dict(zip(('x', 'y', 'width', 'height'), clip)))
        boxes = {}
        for text in t.get('texts', []):
            bb = await page_box(p, text)
            boxes[text] = bb and [round((bb[0] - clip[0]) * SCALE), round((bb[1] - clip[1]) * SCALE),
                                  round(bb[2] * SCALE), round(bb[3] * SCALE)]
            if not bb:
                print('warn %s: text %r not found' % (n, text))
        json.dump({'w': round(clip[2] * SCALE), 'h': round(clip[3] * SCALE), 'boxes': boxes},
                  open(f'{out}/{n}.json', 'w'), indent=1)
        print('ok', n, '%dx%d' % (round(clip[2] * SCALE), round(clip[3] * SCALE)), (await p.title())[:60],
              '| boxes: %d/%d found' % (sum(1 for v in boxes.values() if v), len(boxes)) if boxes else '')
    finally:
        await ctx.close()


async def main(out, tg):
    os.makedirs(out, exist_ok=True)
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        for n, t in tg.items():
            try:
                await shoot_one(b, out, n, t)
            except Exception as e:
                print('fail', n, str(e).split('\n')[0][:150])
        await b.close()


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], targets(sys.argv[2:])))
