// content.js – DeepSeek Provider Bridge (full, with DeepThink support + pre-instruction with PoW/hif + FIXED SSE PARSER v3)
console.log('[Content] DeepSeek Provider Bridge loaded');

let authToken = '';
try {
  const raw = localStorage.getItem('userToken');
  authToken = JSON.parse(raw).value || '';
} catch (e) {}

const WORKER_URL = 'https://fe-static.deepseek.com/chat/static/37627.ebf6d8f55d.js';
const WS_URL = 'ws://localhost:8001/ws?key=supersecretkey';
let ws = null;

function connectWS() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => console.log('[Content] WebSocket connected to backend');
  ws.onmessage = async (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'REQUEST') {
      let result;
      try {
        result = await handleApiRequest(msg.payload);
        console.log(result.content);
      } catch (e) {
        console.error('[Content] handleApiRequest error:', e);
        result = { success: false, error: e.message };
      }
      ws.send(JSON.stringify({
        type: 'RESPONSE',
        requestId: msg.requestId,
        result
      }));
    }
  };
  ws.onclose = () => {
    console.log('[Content] WebSocket disconnected, retrying in 5s');
    setTimeout(connectWS, 5000);
  };
  ws.onerror = (e) => console.error('[Content] WebSocket error', e);
}
connectWS();

async function getPowToken(challengeData) {
  const { algorithm, challenge, salt, difficulty, signature, expire_at } = challengeData;

  const resp = await fetch(WORKER_URL);
  if (!resp.ok) throw new Error('Failed to fetch worker script');
  let code = await resp.text();
  code = code.replace(
    'r.p+"static/sha3_wasm_bg.7b9ca65ddd.wasm"',
    '"https://fe-static.deepseek.com/chat/static/sha3_wasm_bg.7b9ca65ddd.wasm"'
  );
  code = code.replace(/r\.p\+/g, '"https://fe-static.deepseek.com/chat/"');

  const blob = new Blob([code], { type: 'application/javascript' });
  const blobUrl = URL.createObjectURL(blob);
  const worker = new Worker(blobUrl, { type: 'classic' });
  URL.revokeObjectURL(blobUrl);

  worker.postMessage({
    type: 'pow-challenge',
    challenge: { algorithm, challenge, salt, difficulty, signature, expireAt: expire_at }
  });

  const powAnswer = await new Promise((resolve, reject) => {
    worker.onmessage = (e) => {
      if (e.data.type === 'pow-answer') {
        const answerValue = e.data.answer.answer;
        if (typeof answerValue === 'number') resolve(answerValue);
        else reject(new Error('Invalid answer'));
        worker.terminate();
      } else if (e.data.type === 'pow-error') {
        reject(new Error(e.data.error || 'Worker error'));
        worker.terminate();
      }
    };
    worker.onerror = () => { reject(new Error('Worker error')); worker.terminate(); };
    setTimeout(() => { reject(new Error('Timeout')); worker.terminate(); }, 30000);
  });

  const powResponse = {
    algorithm,
    challenge,
    salt,
    answer: powAnswer,
    signature,
    target_path: challengeData.target_path || '/api/v0/chat/completion'
  };
  return btoa(JSON.stringify(powResponse));
}

async function getHifLeim() {
  try {
    const r = await fetch('https://hif-leim.deepseek.com/query', {
      headers: {
        'x-app-version': '2.0.0', 'x-client-platform': 'web', 'x-client-version': '2.0.0',
        'x-client-locale': 'en_US', 'x-client-bundle-id': 'com.deepseek.chat',
        'Origin': 'https://chat.deepseek.com', 'Referer': 'https://chat.deepseek.com/'
      }
    });
    const d = await r.json();
    return d.data.biz_data.value;
  } catch (e) { return ''; }
}

async function createChatSession() {
  if (!authToken) throw new Error('Нет токена');
  const resp = await fetch('https://chat.deepseek.com/api/v0/chat_session/create', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authToken}`,
      'x-app-version': '2.0.0', 'x-client-platform': 'web', 'x-client-version': '2.0.0',
      'x-client-locale': 'en_US', 'x-client-bundle-id': 'com.deepseek.chat',
      'Origin': 'https://chat.deepseek.com', 'Referer': 'https://chat.deepseek.com/'
    },
    body: '{}',
    credentials: 'include'
  });
  const data = await resp.json();
  if (data.code !== 0 || data.data.biz_code !== 0) throw new Error('Failed to create chat session');
  return data.data.biz_data.chat_session.id;
}

async function uploadFile(fileBase64, fileName, powTokenForFile, leim, dliq) {
  const formData = new FormData();
  const fileBlob = await fetch(fileBase64).then(r => r.blob());
  formData.append('file', fileBlob, fileName);

  const resp = await fetch('https://chat.deepseek.com/api/v0/file/upload_file', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${authToken}`,
      'x-ds-pow-response': powTokenForFile,
      'x-hif-dliq': dliq,
      'x-hif-leim': leim,
      'x-app-version': '2.0.0', 'x-client-platform': 'web', 'x-client-version': '2.0.0',
      'x-client-locale': 'en_US', 'x-client-bundle-id': 'com.deepseek.chat',
      'Origin': 'https://chat.deepseek.com', 'Referer': 'https://chat.deepseek.com/',
      'x-file-size': fileBlob.size,
      'x-model-type': 'vision',
      'x-thinking-enabled': '1'
    },
    body: formData,
    credentials: 'include'
  });
  const data = await resp.json();
  if (data.code !== 0 || data.data.biz_code !== 0) throw new Error('Upload failed');
  return data.data.biz_data.id;
}

async function pollFileStatus(fileId) {
  while (true) {
    const resp = await fetch(`https://chat.deepseek.com/api/v0/file/fetch_files?file_ids=${fileId}`, {
      headers: {
        'Authorization': `Bearer ${authToken}`,
        'x-app-version': '2.0.0', 'x-client-platform': 'web', 'x-client-version': '2.0.0',
        'x-client-locale': 'en_US', 'x-client-bundle-id': 'com.deepseek.chat',
        'Origin': 'https://chat.deepseek.com', 'Referer': 'https://chat.deepseek.com/'
      },
      credentials: 'include'
    });
    const data = await resp.json();
    const file = data.data.biz_data.files[0];
    if (file.status === 'SUCCESS') return file;
    if (file.status === 'FAILED') throw new Error('File processing failed');
    await new Promise(r => setTimeout(r, 1000));
  }
}

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function handleApiRequest(payload) {
  console.log('[Content] Payload:', payload);
  const { action, prompt, model_type, search_enabled, thinking_enabled, images } = payload;
  if (!authToken) return { success: false, error: 'Нет токена' };

  // ===== NEW: Support for persistent chat =====
  // Action: create_chat_session
  if (action === 'create_chat_session') {
    try {
      const chatSessionId = await createChatSession();
      return { success: true, chat_session_id: chatSessionId };
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  // Action: continue_chat
  if (action === 'continue_chat') {
    let { chat_session_id, parent_message_id } = payload;
    
    // If no chat_session_id provided, create a new one (first message in chat)
    if (!chat_session_id) {
      console.log('[Content] Creating new chat session for first message');
      try {
        chat_session_id = await createChatSession();
        console.log('[Content] Created chat session:', chat_session_id);
      } catch (e) {
        return { success: false, error: `Failed to create chat: ${e.message}` };
      }
    }

    try {
      // Загрузка файлов (если есть)
      let refFileIds = [];
      if (images && images.length > 0) {
        console.log('[Content] Uploading images for vision:', images.length);
        const leim = await getHifLeim();
        const dliq = 'kFQN21oMyH+18PQ8f6ALewc+ylpuVWKvvQGmumNfTvZRwJHPSFAMZGQ=.vrF/bWtO/a0pR5UP';

        for (const img of images) {
          const filePow = await getPowToken({
            ...await fetchChallengeForPath('/api/v0/file/upload_file'),
            target_path: '/api/v0/file/upload_file'
          });
          const fileId = await uploadFile(img.base64, img.name, filePow, leim, dliq);
          await pollFileStatus(fileId);
          refFileIds.push(fileId);
          await new Promise(r => setTimeout(r, 500));
        }
      }

      // PoW для completion
      const chalData = await fetchChallengeForPath('/api/v0/chat/completion');
      const powToken = await getPowToken({ ...chalData, target_path: '/api/v0/chat/completion' });
      const leim = await getHifLeim();
      const dliq = 'kFQN21oMyH+18PQ8f6ALewc+ylpuVWKvvQGmumNfTvZRwJHPSFAMZGQ=.vrF/bWtO/a0pR5UP';

      const body = JSON.stringify({
        chat_session_id: chat_session_id,
        parent_message_id: parent_message_id || null,  // Use response_message_id from previous response
        model_type: model_type || null,
        prompt: prompt || '',
        ref_file_ids: refFileIds,
        thinking_enabled: false,
        search_enabled: search_enabled ?? false,
        action: null,
        preempt: false
      });

      const headers = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`,
        'x-ds-pow-response': powToken,
        'x-hif-dliq': dliq,
        'x-hif-leim': leim,
        'x-app-version': '2.0.0',
        'x-client-platform': 'web',
        'x-client-version': '2.0.0',
        'x-client-locale': 'en_US',
        'x-client-bundle-id': 'com.deepseek.chat',
        'Origin': 'https://chat.deepseek.com',
        'Referer': 'https://chat.deepseek.com/'
      };

      if (refFileIds.length > 0) {
        headers['x-model-type'] = 'vision';
      } else if (model_type) {
        headers['x-model-type'] = model_type;
      }

      const resp = await fetch('https://chat.deepseek.com/api/v0/chat/completion', {
        method: 'POST',
        headers,
        body,
        credentials: 'include'
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      // Parse SSE response
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let fullContent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        fullContent += decoder.decode(value, { stream: true });
      }

      // Simple SSE parser for content
      const lines = fullContent.split('\n');
      const fragments = [];
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

        if (typeof data.v === 'string' && data.p === undefined) {
          appendLast(data.v);
        } else if (data.v && typeof data.v === 'object' && data.v.response && Array.isArray(data.v.response.fragments)) {
          for (const frag of data.v.response.fragments) pushFrag(frag);
        } else if (data.p === 'response/fragments/-1/content' && data.o === 'APPEND' && typeof data.v === 'string') {
          appendLast(data.v);
        } else if (data.p === 'response/fragments' && data.o === 'APPEND' && Array.isArray(data.v)) {
          for (const frag of data.v) pushFrag(frag);
        }
      }

      const responseFrags = fragments.filter(f => f.type === 'RESPONSE');
      const responseText = responseFrags.map(f => f.content).join('').trim();

      // Extract response_message_id from SSE (needed for next request as parent_message_id)
      let responseMessageId = null;
      let hasError = false;
      
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const data = JSON.parse(line.slice(6));
          
          // Check for errors (rate limit, API errors, expert busy, etc.)
          if (data.type === 'error' || 
              data.finish_reason === 'rate_limit_reached' ||
              data.finish_reason === 'expert_busy_use_default' ||
              (data.content && data.content.includes('Messages too frequent')) ||
              (data.content && data.content.includes('Server is busy'))) {
            hasError = true;
            console.warn('[Content] DeepSeek SSE error detected:', data);
          }
          
          // Look for response_message_id in the SSE data (it's an INTEGER!)
          if (data.response_message_id !== undefined) {
            responseMessageId = data.response_message_id;  // Keep as number, NOT string!
          }
        } catch (e) { /* ignore parse errors */ }
      }

      // Don't return message_id if there was an error (prevents broken chat chain)
      if (hasError) {
        console.warn('[Content] Clearing message_id due to error in SSE stream');
        responseMessageId = null;
      }

      if (!responseMessageId && !hasError) {
        console.warn('[Content] Could not extract response_message_id from DeepSeek SSE!');
      }

      return {
        success: !hasError,
        content: responseText,
        message_id: responseMessageId,  // null if error
        chat_session_id: chat_session_id,
        error: hasError ? 'DeepSeek returned error (rate limit or other)' : undefined
      };
    } catch (e) {
      console.error('[Content] continue_chat error:', e);
      return { success: false, error: e.message };
    }
  }

  // ===== OLD: One-shot request (legacy, create new chat each time) =====
  // Загрузка файлов (если есть)
  let refFileIds = [];
  if (images && images.length > 0) {
    console.log('[Content] Uploading images for vision:', images.length);
    const leim = await getHifLeim();
    const dliq = 'kFQN21oMyH+18PQ8f6ALewc+ylpuVWKvvQGmumNfTvZRwJHPSFAMZGQ=.vrF/bWtO/a0pR5UP';

    for (const img of images) {
      const filePow = await getPowToken({
        ...await fetchChallengeForPath('/api/v0/file/upload_file'),
        target_path: '/api/v0/file/upload_file'
      });
      try {
        const fileId = await uploadFile(img.base64, img.name, filePow, leim, dliq);
        console.log('[Content] File uploaded:', fileId);
        const st = await pollFileStatus(fileId);
        console.log('[Content] File status after poll:', fileId, st);
        refFileIds.push(fileId);
      } catch (e) {
        console.error('[Content] Upload error for', img.name, ':', e);
        return { success: false, error: 'Upload failed: ' + (e && e.message ? e.message : e) };
      }
      await new Promise(r => setTimeout(r, 500));
    }
    console.log('[Content] All files ready, refFileIds:', refFileIds);
  }

  // 1. Создаём одну сессию
  const chatSessionId = await createChatSession();

  // 2. Опциональный pre-instruction. По умолчанию ВЫКЛ: agent-x — чистый
  // провайдер, tool-calling задаётся системным промптом от Hermes, а не
  // костылём расширения. Включить через payload.pre_instruction=true.
  let mainParentId = null;
  if (payload.pre_instruction) {
    const preChalData = await fetchChallengeForPath('/api/v0/chat/completion');
    const prePowToken = await getPowToken({ ...preChalData, target_path: '/api/v0/chat/completion' });
    const preLeim = await getHifLeim();
    const preDliq = 'kFQN21oMyH+18PQ8f6ALewc+ylpuVWKvvQGmumNfTvZRwJHPSFAMZGQ=.vrF/bWtO/a0pR5UP';

    const preBody = JSON.stringify({
      chat_session_id: chatSessionId,
      parent_message_id: null,
      model_type: model_type || null,
      prompt: "**Remember YOUR MAIN MISSION: if you need a tool, respond ONLY with the JSON object. No other text. LIKE THIS: {\"tool_calls\": [{\"name\": \"<tool_name>\", \"arguments\": {<parameters>}}]}**",
      ref_file_ids: [],
      thinking_enabled: false,
      search_enabled: false,
      action: null,
      preempt: false
    });

    console.log('[Content] Sending pre-instruction to chat', chatSessionId);
    const preResp = await fetch('https://chat.deepseek.com/api/v0/chat/completion', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`,
        'x-ds-pow-response': prePowToken,
        'x-hif-dliq': preDliq,
        'x-hif-leim': preLeim,
        'x-app-version': '2.0.0', 'x-client-platform': 'web', 'x-client-version': '2.0.0',
        'x-client-locale': 'en_US', 'x-client-bundle-id': 'com.deepseek.chat',
        'Origin': 'https://chat.deepseek.com', 'Referer': 'https://chat.deepseek.com/'
      },
      body: preBody,
      credentials: 'include'
    });
    await preResp.text();
    mainParentId = 2; // ответ на pre-instruction
    await sleep(2000);
  }

  // 3. Основной промпт (с PoW, hif). parent_message_id зависит от pre_instruction.
  const chalData = await fetchChallengeForPath('/api/v0/chat/completion');
  const powToken = await getPowToken({ ...chalData, target_path: '/api/v0/chat/completion' });
  const leim = await getHifLeim();
  const dliq = 'kFQN21oMyH+18PQ8f6ALewc+ylpuVWKvvQGmumNfTvZRwJHPSFAMZGQ=.vrF/bWtO/a0pR5UP';

  const body = JSON.stringify({
    chat_session_id: chatSessionId,
    parent_message_id: mainParentId,
    model_type: model_type || null,
    prompt: prompt || '',
    ref_file_ids: refFileIds,
    thinking_enabled: false,
    search_enabled: search_enabled ?? false,
    action: null,
    preempt: false
  });

  console.log('[Content] Final request body:', body);

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${authToken}`,
    'x-ds-pow-response': powToken,
    'x-hif-dliq': dliq,
    'x-hif-leim': leim,
    'x-app-version': '2.0.0', 'x-client-platform': 'web', 'x-client-version': '2.0.0',
    'x-client-locale': 'en_US', 'x-client-bundle-id': 'com.deepseek.chat',
    'Origin': 'https://chat.deepseek.com', 'Referer': 'https://chat.deepseek.com/'
  };

  if (refFileIds.length > 0) {
    headers['x-model-type'] = 'vision';
  } else if (model_type) {
    headers['x-model-type'] = model_type;
  }

  const resp = await fetch('https://chat.deepseek.com/api/v0/chat/completion', {
    method: 'POST',
    headers,
    body,
    credentials: 'include'
  });

  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);

  // === УНИВЕРСАЛЬНЫЙ ПАРСЕР SSE (v3 — собирает ВСЁ, включая вложенные fragments) ===
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let fullContent = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    fullContent += decoder.decode(value, { stream: true });
  }

  // DEBUG: return the raw SSE stream verbatim for format study
  if (payload.debug_raw) {
    return { success: true, content: fullContent, raw: true };
  }

  // === ПАРСЕР SSE v4 — раздельно: thinking / content / search-результаты ===
  // Формат DeepSeek (по сырым захватам):
  //  - первый объект {"v":{"response":{fragments:[{type,content}]}}} задаёт фрагмент
  //  - голые {"v":"txt"} аппендятся в content ПОСЛЕДНЕГО фрагмента
  //  - {"p":"response/fragments/-1/content","o":"APPEND","v":"txt"} — то же явно
  //  - {"p":"response","o":"BATCH","v":[{"p":"fragments","o":"APPEND","v":[{...}]}]} — новый фрагмент
  //  - {"p":"response/fragments/-1/results","v":[...]} — результаты поиска
  //  - типы фрагментов: RESPONSE (ответ), THINK (рассуждения), SEARCH (поиск)
  //  - цитаты в тексте: [citation:N], N = cite_index из results
  const lines = fullContent.split('\n');

  const fragments = [];          // {type, content}
  let searchResults = [];        // [{url,title,snippet,cite_index,site_name}]

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

    // 1. Голые дельты {"v":"txt"} -> в последний фрагмент
    if (typeof data.v === 'string' && data.p === undefined) {
      appendLast(data.v);
      continue;
    }

    // 2. Полный начальный объект response.fragments
    if (data.v && typeof data.v === 'object' && data.v.response && Array.isArray(data.v.response.fragments)) {
      for (const frag of data.v.response.fragments) {
        pushFrag(frag);
        if (Array.isArray(frag.results) && frag.results.length) searchResults = searchResults.concat(frag.results);
      }
      continue;
    }

    // 3. APPEND в контент последнего фрагмента
    if (data.p === 'response/fragments/-1/content' && data.o === 'APPEND' && typeof data.v === 'string') {
      appendLast(data.v);
      continue;
    }

    // 4. Результаты поиска
    if (typeof data.p === 'string' && data.p.endsWith('/results') && Array.isArray(data.v)) {
      searchResults = searchResults.concat(data.v);
      continue;
    }

    // 4b. Прямой APPEND нового фрагмента: {"p":"response/fragments","o":"APPEND","v":[{...}]}
    if (data.p === 'response/fragments' && data.o === 'APPEND' && Array.isArray(data.v)) {
      for (const frag of data.v) {
        pushFrag(frag);
        if (Array.isArray(frag.results) && frag.results.length) searchResults = searchResults.concat(frag.results);
      }
      continue;
    }

    // 5. BATCH-обновления: может добавлять новые фрагменты
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

  // Собираем по типам
  const thinking = fragments.filter(f => f.type === 'THINK').map(f => f.content).join('');
  const answer = fragments.filter(f => f.type === 'RESPONSE').map(f => f.content).join('');

  // Дедуп результатов поиска по url
  const seen = new Set();
  const citations = [];
  for (const r of searchResults) {
    if (!r || !r.url || seen.has(r.url)) continue;
    seen.add(r.url);
    citations.push({
      index: r.cite_index, url: r.url, title: r.title,
      snippet: r.snippet, site_name: r.site_name
    });
  }

  const finalAnswer = (answer || '').trim();
  if (!finalAnswer && !thinking) {
    const diag = `frags=${fragments.length} types=${fragments.map(f=>f.type).join(',')} refFiles=${refFileIds.length} sse_len=${fullContent.length} sse_head=${fullContent.slice(0,300)}`;
    console.error('[Content] EMPTY answer:', diag);
    return { success: false, error: 'Пустой ответ от DeepSeek | ' + diag };
  }

  return {
    success: true,
    content: finalAnswer || thinking.trim(),
    thinking: thinking.trim() || undefined,
    citations: citations.length ? citations : undefined,
    refFiles: refFileIds.length
  };
}

async function fetchChallengeForPath(targetPath) {
  const resp = await fetch('https://chat.deepseek.com/api/v0/chat/create_pow_challenge', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authToken}`,
      'x-app-version': '2.0.0', 'x-client-platform': 'web', 'x-client-version': '2.0.0',
      'x-client-locale': 'en_US', 'x-client-bundle-id': 'com.deepseek.chat',
      'Origin': 'https://chat.deepseek.com', 'Referer': 'https://chat.deepseek.com/'
    },
    body: JSON.stringify({ target_path: targetPath }),
    credentials: 'include'
  });
  const data = await resp.json();
  return data.data.biz_data.challenge;
}