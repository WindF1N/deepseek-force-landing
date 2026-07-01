// test_sse_parser.js — проверка парсера на реальных сырых потоках.
// Запуск: node chrome-extension/test_sse_parser.js
const fs = require('fs');
const path = require('path');
const { parseDeepSeekSSE } = require('./sse_parser');

const FIX = path.join(__dirname, '..', 'backend', 'tests', 'fixtures');
let failures = 0;
function check(name, cond) {
  console.log((cond ? 'PASS ' : 'FAIL ') + name);
  if (!cond) failures++;
}

// --- NORMAL ---
const normal = parseDeepSeekSSE(fs.readFileSync(path.join(FIX, 'sse_normal.txt'), 'utf8'));
check('normal: success', normal.success === true);
check('normal: has content', normal.content.length > 0);
check('normal: no thinking', normal.thinking === undefined);
check('normal: no citations', normal.citations === undefined);
check('normal: content has answer 42', /42/.test(normal.content));
console.log('   normal.content =', JSON.stringify(normal.content));

// --- THINKING ---
const think = parseDeepSeekSSE(fs.readFileSync(path.join(FIX, 'sse_thinking.txt'), 'utf8'));
check('thinking: success', think.success === true);
check('thinking: has thinking block', !!think.thinking && think.thinking.length > 0);
check('thinking: has separate answer', think.content.length > 0);
check('thinking: thinking != content', think.thinking !== think.content);
console.log('   thinking.content =', JSON.stringify(think.content.slice(0, 120)));
console.log('   thinking.thinking[0:80] =', JSON.stringify((think.thinking || '').slice(0, 80)));

// --- SEARCH ---
const search = parseDeepSeekSSE(fs.readFileSync(path.join(FIX, 'sse_search.txt'), 'utf8'));
check('search: success', search.success === true);
check('search: has content', search.content.length > 0);
check('search: has citations', Array.isArray(search.citations) && search.citations.length > 0);
check('search: citation has url', search.citations && /^https?:\/\//.test(search.citations[0].url));
check('search: citation has title', search.citations && !!search.citations[0].title);
console.log('   search.content =', JSON.stringify(search.content.slice(0, 160)));
console.log('   search.citations.length =', search.citations ? search.citations.length : 0);

console.log('\n' + (failures === 0 ? 'ALL PASSED' : failures + ' FAILURES'));
process.exit(failures === 0 ? 0 : 1);
