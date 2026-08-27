import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(process.cwd(), 'src');
const exts = new Set(['.ts', '.tsx', '.js', '.jsx']);

const patterns = [
  /lang\s*===\s*['\"]zh['\"]/g,
  /lang\s*===\s*['\"]en['\"]/g,
  /['\"`](Loading|Error|Retry|Back|No data|No records|Select|Clear|Apply|Search|View|Page)\b/g,
];

function walk(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    const st = fs.statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (exts.has(path.extname(p))) out.push(p);
  }
  return out;
}

const files = fs.existsSync(ROOT) ? walk(ROOT) : [];
let hit = 0;

for (const f of files) {
  const s = fs.readFileSync(f, 'utf8');
  const lines = s.split(/\r?\n/);

  patterns.forEach((re) => {
    lines.forEach((line, i) => {
      if (re.test(line)) {
        console.log(`${f}:${i + 1}: ${line.trim()}`);
        hit++;
      }
      re.lastIndex = 0;
    });
  });
}

console.log(`\nFound ${hit} potential i18n issues.`);
process.exitCode = hit ? 1 : 0;
