#!/usr/bin/env python3
"""Record a live website as a smooth screen-recording mp4 (scroll-driven animations included).

    python3 scripts/plumbing/record.py assets/video/site.mp4 https://site.com steps.json [--view 1440x900] [--fps 30]

Uses Chrome DevTools screencast (full-quality JPEG frames with timestamps), not Playwright's recordVideo
(low-bitrate VP8, blurry). Frames are laid on a constant 30 fps timeline and encoded with keyframes every
1 s (HyperFrames needs that). The video starts at the first step, not at page load.

steps.json: a list of steps, run in order:
  ["wait", s]                         hold still for s seconds
  ["scroll", "css selector", s]       smooth-scroll (eased) so that element's top is at the viewport top
  ["scrollby", px, s]                 smooth-scroll by px
  ["scrollto", y, s]                  smooth-scroll to absolute y
  ["drag", "css selector", f0, f1, s] drag with the mouse across that element from fraction f0 to f1 of its width
  ["click", "css selector"]           click it
  ["hover", "css selector"]           move the mouse over it
Prints the timeline (when each step starts in the output video) so scenes can sync to it.
"""
import asyncio, base64, json, os, shutil, subprocess, sys, tempfile, time

from playwright.async_api import async_playwright

EASE = """async ([to, ms]) => { const y0 = scrollY, d = to - y0, t0 = performance.now();
  await new Promise(r => { const f = now => { const p = Math.min(1, (now - t0) / ms);
    const e = p < .5 ? 4*p*p*p : 1 - Math.pow(-2*p + 2, 3) / 2; scrollTo(0, y0 + d*e);
    p < 1 ? requestAnimationFrame(f) : r(); }; requestAnimationFrame(f); }); }"""


async def main(out, url, steps, vw, vh, fps):
    tmp = tempfile.mkdtemp(prefix='rec-')
    frames = []
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        ctx = await b.new_context(viewport={'width': vw, 'height': vh}, device_scale_factor=1)
        page = await ctx.new_page()
        await page.goto(url, wait_until='networkidle')
        await page.wait_for_timeout(1500)
        cdp = await ctx.new_cdp_session(page)

        async def on_frame(ev):
            ts = ev['metadata']['timestamp']
            p = os.path.join(tmp, '%05d.jpg' % len(frames))
            frames.append((ts, p))
            open(p, 'wb').write(base64.b64decode(ev['data']))
            await cdp.send('Page.screencastFrameAck', {'sessionId': ev['sessionId']})

        cdp.on('Page.screencastFrame', lambda ev: asyncio.ensure_future(on_frame(ev)))
        await cdp.send('Page.startScreencast', {'format': 'jpeg', 'quality': 95, 'maxWidth': vw, 'maxHeight': vh,
                                                'everyNthFrame': 1})
        await page.wait_for_timeout(300)
        t0 = time.time()
        log = []
        for st in steps:
            log.append((time.time() - t0, st))
            op = st[0]
            if op == 'wait':
                await page.wait_for_timeout(st[1] * 1000)
            elif op in ('scroll', 'scrollby', 'scrollto'):
                if op == 'scroll':
                    to = await page.eval_on_selector(st[1], 'e => e.getBoundingClientRect().top + scrollY')
                elif op == 'scrollby':
                    to = await page.evaluate('scrollY') + st[1]
                else:
                    to = st[1]
                await page.evaluate(EASE, [to, st[2] * 1000])
            elif op == 'drag':
                bx = await page.locator(st[1]).bounding_box()
                y = bx['y'] + bx['height'] / 2
                await page.mouse.move(bx['x'] + bx['width'] * st[2], y)
                await page.mouse.down()
                n = max(2, int(st[4] * 15))
                for i in range(1, n + 1):
                    f = st[2] + (st[3] - st[2]) * i / n
                    await page.mouse.move(bx['x'] + bx['width'] * f, y)
                    await page.wait_for_timeout(st[4] * 1000 / n)
                await page.mouse.up()
            elif op == 'click':
                await page.click(st[1])
            elif op == 'hover':
                await page.hover(st[1])
        total = time.time() - t0
        await page.wait_for_timeout(200)
        await cdp.send('Page.stopScreencast')
        await b.close()
    # constant-fps timeline from t0: each output frame shows the latest captured frame
    frames.sort()
    n_out = int(total * fps)
    lst = os.path.join(tmp, 'list.txt')
    j = 0
    with open(lst, 'w') as f:
        for i in range(n_out):
            t = t0 + i / fps
            while j + 1 < len(frames) and frames[j + 1][0] <= t:
                j += 1
            f.write("file '%s'\nduration %.6f\n" % (frames[j][1], 1 / fps))
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', lst, '-r', str(fps),
                    '-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-pix_fmt', 'yuv420p', '-g', str(fps),
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-an', out], check=True)
    shutil.rmtree(tmp)
    print('%s  %.2f s, %d captured frames' % (out, total, len(frames)))
    for t, st in log:
        print('  %6.2f  %s' % (t, json.dumps(st)))


if __name__ == '__main__':
    a = sys.argv[1:]
    view, fps = '1440x900', 30
    if '--view' in a:
        i = a.index('--view'); view = a[i + 1]; del a[i:i + 2]
    if '--fps' in a:
        i = a.index('--fps'); fps = int(a[i + 1]); del a[i:i + 2]
    out, url, steps = a[0], a[1], json.load(open(a[2]))
    vw, vh = map(int, view.split('x'))
    asyncio.run(main(out, url, steps, vw, vh, fps))
