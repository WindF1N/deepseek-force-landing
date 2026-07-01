// sse_parser.js — отделённый от content.js парсер SSE-потока DeepSeek.
// Чистая функция без зависимостей от браузера, чтобы её можно было тестировать
// на сохранённых сырых потоках (backend/tests/fixtures/sse_*.txt).
//
// Формат DeepSeek (по сырым захватам, см. SCALING/анализ):
//  - первый объект {"v":{"response":{fragments:[{type,content}]}}} задаёт фрагмент
//  - голые {"v":"txt"} аппендятся в content ПОСЛЕДНЕГО фрагмента
//  - {"p":"response/fragments/-1/content","o":"APPEND","v":"txt"} — то же явно
//  - {"p":"response","o":"BATCH","v":[{"p":"fragments","o":"APPEND","v":[{...}]}]} — новый фрагмент
//  - {"p":"response/fragments/-1/results","v":[...]} — результаты поиска
//  - типы фрагментов: RESPONSE (ответ), THINK (рассуждения), SEARCH (поиск)
//  - цитаты в тексте: [citation:N], N = cite_index из results

function parseDeepSeekSSE(fullContent) {
  const lines = fullContent.split('\n');
  const fragments = [];
  let searchResults = [];

  const lastFrag = () => fragments.length ? fragments[fragments.length - 1] : null;
  const pushFrag = (f) => fragments.push({ type: f.type || 'RESPONSE', content: f.content || '' });
  const appendLast = (txt) => {
    let lf = lastFrag();
    if (!lf) { pushFrag({ type: 'RESPONSE', content: '' }); lf = lastFrag(); }
    lf.content += txt;
  };

  for (const line of lines) {
    if (!line.startsWith('data: ')) continue;
    let data;
    try { data = JSON.parse(line.slice(6)); } catch (e) { continue; }

    if (typeof data.v === 'string' && data.p === undefined) { appendLast(data.v); continue; }

    if (data.v && typeof data.v === 'object' && data.v.response && Array.isArray(data.v.response.fragments)) {
      for (const frag of data.v.response.fragments) {
        pushFrag(frag);
        if (Array.isArray(frag.results) && frag.results.length) searchResults = searchResults.concat(frag.results);
      }
      continue;
    }

    if (data.p === 'response/fragments/-1/content' && data.o === 'APPEND' && typeof data.v === 'string') {
      appendLast(data.v); continue;
    }

    if (typeof data.p === 'string' && data.p.endsWith('/results') && Array.isArray(data.v)) {
      searchResults = searchResults.concat(data.v); continue;
    }

    // Прямой APPEND нового фрагмента: {"p":"response/fragments","o":"APPEND","v":[{...}]}
    if (data.p === 'response/fragments' && data.o === 'APPEND' && Array.isArray(data.v)) {
      for (const frag of data.v) {
        pushFrag(frag);
        if (Array.isArray(frag.results) && frag.results.length) searchResults = searchResults.concat(frag.results);
      }
      continue;
    }

    if (data.p === 'response' && data.o === 'BATCH' && Array.isArray(data.v)) {
      for (const op of data.v) {
        if (op.p === 'fragments' && op.o === 'APPEND' && Array.isArray(op.v)) {
          for (const frag of op.v) {
            pushFrag(frag);
            if (Array.isArray(frag.results) && frag.results.length) searchResults = searchResults.concat(frag.results);
          }
        }
      }
      continue;
    }
  }

  const thinking = fragments.filter(f => f.type === 'THINK').map(f => f.content).join('');
  const answer = fragments.filter(f => f.type === 'RESPONSE').map(f => f.content).join('');

  const seen = new Set();
  const citations = [];
  for (const r of searchResults) {
    if (!r || !r.url || seen.has(r.url)) continue;
    seen.add(r.url);
    citations.push({ index: r.cite_index, url: r.url, title: r.title, snippet: r.snippet, site_name: r.site_name });
  }

  const finalAnswer = (answer || '').trim();
  return {
    success: !!(finalAnswer || thinking),
    content: finalAnswer,
    thinking: thinking.trim() || undefined,
    citations: citations.length ? citations : undefined
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { parseDeepSeekSSE };
}
