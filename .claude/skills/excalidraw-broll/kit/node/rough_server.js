// Line protocol over stdin/stdout, one JSON object per line.
//   {"op": "rectangle", "args": [...], "o": {...}}  -> [{d, stroke, sw, fill}]   (any rough.js generator op)
//   {"op": "icon", "args": ["notion"]}               -> {title, hex, svg}        (simple-icons by slug)
// Paths are rounded to 1 decimal to keep the generated HTML small. Deterministic given o.seed.
const rough = require('roughjs/bundled/rough.cjs.js');
const gen = rough.generator();
let icons = null;
const rl = require('readline').createInterface({ input: process.stdin });
const r1 = (d) => d.replace(/(-?\d+\.\d)\d+/g, '$1');
rl.on('line', (line) => {
  let out;
  try {
    const q = JSON.parse(line);
    if (q.op === 'icon') {
      icons = icons || Object.values(require('simple-icons')).filter((x) => x && x.slug);
      const v = icons.find((x) => x.slug === q.args[0]);
      out = v ? { title: v.title, hex: v.hex, svg: v.svg } : { error: 'no simple-icon with slug ' + q.args[0] };
    } else {
      const dr = gen[q.op](...q.args, q.o);
      out = gen.toPaths(dr).map((p) => ({ d: r1(p.d), stroke: p.stroke, sw: p.strokeWidth, fill: p.fill }));
    }
  } catch (e) {
    out = { error: String(e) };
  }
  process.stdout.write(JSON.stringify(out) + '\n');
});
