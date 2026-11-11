#!/usr/bin/env python3
"""SUPERSEDED: use shoot.py targets with "texts" (crops at capture, writes <name>.json, read by k.shot()).
Screenshot pages and measure where given texts sit on them (for drawing highlights).

Usage: python3 boxes.py targets.json IMG_DIR BOXES_JSON
  targets.json: {"name": {"url": "https://...", "texts": ["Headline text", "a phrase"]}}
Writes IMG_DIR/<name>.png (top MAXH css px of the page, VIEW width) and BOXES_JSON:
  {"name": {"h_css": page height captured, "boxes": {"Headline text": [x, y, w, h]}}}
Boxes are fractions of the captured image (x/width, y/height), null if the text was not found.
Use core.box() / core.box_px() to read them. Always measure; guessed coordinates are wrong.
Env: VIEW, SCALE, WAIT as in shoot.py; MAXH=3000.
"""
import asyncio, json, os, sys

from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shoot import VW, browser_context, open_page, targets  # noqa: E402

MAXH = int(os.environ.get('MAXH', '3000'))


async def main(tg, img_dir, out_json):
    os.makedirs(img_dir, exist_ok=True)
    out = json.load(open(out_json)) if os.path.exists(out_json) else {}
    async with async_playwright() as pw:
        b, ctx = await browser_context(pw)
        for n, t in tg.items():
            try:
                p = await open_page(ctx, t['url'])
                H = min(await p.evaluate('document.documentElement.scrollHeight'), MAXH)
                await p.screenshot(path=f'{img_dir}/{n}.png', full_page=True, clip={'x': 0, 'y': 0, 'width': VW, 'height': H})
                boxes = {}
                for text in t.get('texts', []):
                    bb = None
                    try:
                        bb = await p.get_by_text(text, exact=False).first.bounding_box(timeout=3000)
                    except Exception:
                        pass
                    boxes[text] = bb and [round(bb['x'] / VW, 4), round(bb['y'] / H, 4),
                                          round(bb['width'] / VW, 4), round(bb['height'] / H, 4)]
                out[n] = {'h_css': H, 'boxes': boxes}
                print(n, H, boxes)
                await p.close()
            except Exception as e:
                print('fail', n, str(e).split('\n')[0][:150])
        await b.close()
    json.dump(out, open(out_json, 'w'), indent=1)


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    asyncio.run(main(targets(sys.argv[1:2]), sys.argv[2], sys.argv[3]))
