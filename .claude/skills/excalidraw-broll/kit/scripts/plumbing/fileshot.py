#!/usr/bin/env python3
"""Screenshot real local files (skills, scripts, code) or real command output, for B-roll.

Usage (from the broll/ folder):
  python3 scripts/plumbing/fileshot.py OUT_DIR name=/path/to/file [name2=/path ...]
  python3 scripts/plumbing/fileshot.py OUT_DIR --cmd name="phantom --help"
Writes OUT_DIR/<name>.png (2000x1250, via shoot.py): the first LINES lines in a light editor look, or the
command's output in a dark terminal look. Env: LINES=36  TITLE_PARTS=2 (path parts shown in the tab).
Check the image for secrets before using it.
"""
import html, os, subprocess, sys, tempfile, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
LINES = int(os.environ.get('LINES', '36'))
PARTS = int(os.environ.get('TITLE_PARTS', '2'))
FONT = '"JetBrains Mono","DejaVu Sans Mono",monospace'


def editor(title, text):
    rows = ''.join('<div class="r"><span class="n">%d</span>%s</div>' % (i + 1, html.escape(l[:118]) or '&nbsp;')
                   for i, l in enumerate(text.split('\n')[:LINES]))
    return ('<!doctype html><meta charset=utf-8><style>body{margin:0;background:#fff;color:#1e1e1e;font:23px/1.42 %s}'
            '.tab{background:#f1f3f5;padding:14px 26px;font-size:22px;border-bottom:1px solid #8884}'
            '.r{white-space:pre;padding-left:8px}.n{display:inline-block;width:56px;text-align:right;color:#adb5bd;'
            'margin-right:26px}.c{padding-top:10px}</style><div class=tab>%s</div><div class=c>%s</div>'
            % (FONT, html.escape(title), rows))


def term(cmd, text):
    body = html.escape('\n'.join(l[:120] for l in text.split('\n')[:LINES + 4]))
    return ('<!doctype html><meta charset=utf-8><style>body{margin:0;background:#1e1e2e;color:#e6e6ef;font:21px/1.4 %s;'
            'padding:22px 30px;white-space:pre}b{color:#a6e3a1}</style><b>$ %s</b>\n%s' % (FONT, html.escape(cmd), body))


def main(argv):
    out, args = argv[0], argv[1:]
    is_cmd = bool(args) and args[0] == '--cmd'
    if is_cmd:
        args = args[1:]
    tmp = tempfile.mkdtemp(prefix='fileshot-')
    targets = []
    for a in args:
        name, val = a.split('=', 1)
        if is_cmd:
            r = subprocess.run(val, shell=True, capture_output=True, text=True)
            page = term(val, r.stdout or r.stderr)
        else:
            p = os.path.expanduser(val)
            page = editor('/'.join(p.split('/')[-PARTS:]), open(p, errors='replace').read())
        f = os.path.join(tmp, name + '.html')
        open(f, 'w').write(page)
        targets.append('%s=file://%s' % (name, urllib.parse.quote(f)))
    env = dict(os.environ, WAIT=os.environ.get('WAIT', '800'))
    subprocess.run([sys.executable, os.path.join(HERE, 'shoot.py'), out] + targets, env=env, check=True)


if __name__ == '__main__':
    main(sys.argv[1:])
