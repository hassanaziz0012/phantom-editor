#!/usr/bin/env python3
"""Screenshot web pages with Playwright: <name>.png (viewport) and <name>-tall.png (top of the full page).

Usage:
  python3 shoot.py OUT_DIR name=https://example.com other=https://...
  python3 shoot.py OUT_DIR targets.json          # {"name": "url"} or {"name": {"url": ..., "texts": [...]}}
Options (env): VIEW=1600x1000  SCALE=1.25  TALL=3200 (css px)  WAIT=4500 (ms)
Images are VIEW * SCALE pixels (default 2000x1250). Cookie banners are clicked away when possible.
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


async def main(out, tg):
    os.makedirs(out, exist_ok=True)
    async with async_playwright() as pw:
        b, ctx = await browser_context(pw)
        for n, t in tg.items():
            try:
                p = await open_page(ctx, t['url'])
                await p.screenshot(path=f'{out}/{n}.png')
                h = await p.evaluate('document.documentElement.scrollHeight')
                await p.screenshot(path=f'{out}/{n}-tall.png', full_page=True,
                                   clip={'x': 0, 'y': 0, 'width': VW, 'height': min(h, TALL)})
                print('ok', n, (await p.title())[:80])
                await p.close()
            except Exception as e:
                print('fail', n, str(e).split('\n')[0][:150])
        await b.close()


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], targets(sys.argv[2:])))
