/* ═══════════════════════════════════════════════════════════════
   AI Multi-Agent Admin — Frontend Controller
   Handles: WebSocket, text/voice/video chat, draggable popups
═══════════════════════════════════════════════════════════════ */
'use strict';

// ─── State ───────────────────────────────────────────────────────────────────
const state = {
  ws: null,
  wsRetries: 0,
  maxRetries: 6,
  chatHistory: [],
  mediaRecorder: null,
  audioChunks: [],
  isRecording: false,
  cameraStream: null,
  pendingSnapshotB64: null,
  pendingFileContent: null,
  currentMode: 'text',
  theme: 'dark',
  sessionId: null,
};

// ─── DOM refs ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const chatPopup     = $('chatPopup');
const videoPopup    = $('videoPopup');
const chatMessages  = $('chatMessages');
const textInput     = $('textInput');
const agentSelect   = $('agentSelect');
const statusDot     = $('statusDot');
const statusLabel   = $('statusLabel');
const snapshotPreview = $('snapshotPreview');
const snapshotImg   = $('snapshotImg');
const voiceStatus   = $('voiceStatus');
const waveform      = $('waveform');
const localVideo    = $('localVideo');
const liveCamFeed   = $('liveCamFeed');

// ─── WebSocket ────────────────────────────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url   = `${proto}://${location.host}/ws`;
  state.ws = new WebSocket(url);

  state.ws.onopen = () => {
    setStatus('online', 'Connected');
    state.wsRetries = 0;
    heartbeat();
  };

  state.ws.onclose = () => {
    setStatus('', 'Disconnected');
    const delay = Math.min(2000 * 2 ** state.wsRetries, 30000);
    state.wsRetries++;
    setTimeout(connectWS, delay);
  };

  state.ws.onerror = () => setStatus('error', 'WS Error');

  state.ws.onmessage = ({ data }) => {
    const msg = JSON.parse(data);
    handleWSMessage(msg);
  };
}

function sendWS(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(obj));
    return true;
  }
  return false;
}

function heartbeat() {
  setTimeout(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      sendWS({ type: 'ping' });
      heartbeat();
    }
  }, 25000);
}

// ─── WS message handler ───────────────────────────────────────────────────────
let streamBuffer = '';
let streamEl = null;

function handleWSMessage(msg) {
  switch (msg.type) {
    case 'start':
      streamBuffer = '';
      streamEl = appendMessage('ai', '');
      showTyping(false);
      break;

    case 'chunk':
      streamBuffer += msg.text;
      if (streamEl) {
        streamEl.querySelector('.msg-body').innerHTML = renderMarkdown(streamBuffer);
        chatMessages.scrollTop = chatMessages.scrollHeight;
      }
      break;

    case 'done':
      if (streamEl) {
        const body = streamEl.querySelector('.msg-body');
        body.innerHTML = renderMarkdown(msg.full_text);
        addCopyButtons(body);
        chatMessages.scrollTop = chatMessages.scrollHeight;
      }
      state.chatHistory.push({ role: 'assistant', content: msg.full_text });
      streamEl = null;
      break;

    case 'transcript':
      appendMessage('user', `🎙 ${msg.text}`);
      state.chatHistory.push({ role: 'user', content: msg.text });
      showTyping(true);
      break;

    case 'voice_response':
      showTyping(false);
      appendMessage('ai', msg.text);
      state.chatHistory.push({ role: 'assistant', content: msg.text });
      playAudio(msg.audio_b64);
      break;

    case 'snapshot_response':
      showTyping(false);
      appendMessage('ai', msg.text);
      break;

    case 'pong':
      break;

    default:
      if (msg.error) appendMessage('ai', `⚠ ${msg.error}`);
  }
}

// ─── Text chat ────────────────────────────────────────────────────────────────
async function sendTextMessage() {
  const text = textInput.value.trim();
  if (!text) return;
  textInput.value = '';
  autoResizeTextarea();

  appendMessage('user', text);
  state.chatHistory.push({ role: 'user', content: text });

  const agent = agentSelect.value;
  let fullText = text;
  if (state.pendingFileContent) {
    fullText += state.pendingFileContent;
    state.pendingFileContent = null;
  }
  const payload = {
    message: fullText,
    agent,
    history: state.chatHistory.slice(-20),
  };
  if (state.pendingSnapshotB64) {
    payload.image_b64 = state.pendingSnapshotB64;
    clearSnapshot();
  }

  showTyping(true);

  const sent = sendWS({ type: 'chat', payload });
  if (!sent) {
    // Fallback to HTTP
    showTyping(false);
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      appendMessage('ai', data.response);
      state.chatHistory.push({ role: 'assistant', content: data.response });
    } catch (e) {
      appendMessage('ai', `⚠ Network error: ${e.message}`);
    }
  }
}

// ─── Voice recording ─────────────────────────────────────────────────────────
async function startRecording() {
  if (state.isRecording) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    state.audioChunks = [];
    state.mediaRecorder.ondataavailable = e => { if (e.data.size) state.audioChunks.push(e.data); };
    state.mediaRecorder.onstop = sendAudio;
    state.mediaRecorder.start();
    state.isRecording = true;
    $('btnMic').classList.add('recording');
    voiceStatus.textContent = 'Recording… tap to stop';
    animateWaveform(true);
  } catch (e) {
    voiceStatus.textContent = 'Mic access denied';
  }
}

function stopRecording() {
  if (!state.isRecording) return;
  state.mediaRecorder.stop();
  state.mediaRecorder.stream.getTracks().forEach(t => t.stop());
  state.isRecording = false;
  $('btnMic').classList.remove('recording');
  voiceStatus.textContent = 'Processing…';
  animateWaveform(false);
}

async function sendAudio() {
  const blob = new Blob(state.audioChunks, { type: 'audio/webm' });
  const reader = new FileReader();
  reader.onloadend = () => {
    const b64 = reader.result.split(',')[1];
    showTyping(true);
    const sent = sendWS({
      type: 'voice',
      payload: { audio_b64: b64, ext: '.webm', agent: agentSelect.value },
    });
    if (!sent) {
      voiceStatus.textContent = 'WS unavailable — use text mode';
      showTyping(false);
    } else {
      voiceStatus.textContent = 'Tap to record';
    }
  };
  reader.readAsDataURL(blob);
}

// ─── Camera / Snapshot ────────────────────────────────────────────────────────
async function startCamera(videoEl) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true });
    videoEl.srcObject = stream;
    state.cameraStream = stream;
    return stream;
  } catch (e) {
    alert('Camera access denied or unavailable.');
    return null;
  }
}

function stopCamera() {
  if (state.cameraStream) {
    state.cameraStream.getTracks().forEach(t => t.stop());
    state.cameraStream = null;
  }
}

function captureSnapshot(videoEl) {
  const canvas = document.createElement('canvas');
  canvas.width  = videoEl.videoWidth  || 640;
  canvas.height = videoEl.videoHeight || 480;
  canvas.getContext('2d').drawImage(videoEl, 0, 0);
  return canvas.toDataURL('image/jpeg', 0.85).split(',')[1];
}

function setSnapshotPreview(b64) {
  state.pendingSnapshotB64 = b64;
  snapshotImg.src = `data:image/jpeg;base64,${b64}`;
  snapshotPreview.classList.remove('hidden');
}

function clearSnapshot() {
  state.pendingSnapshotB64 = null;
  snapshotPreview.classList.add('hidden');
  snapshotImg.src = '';
}

// ─── UI helpers ───────────────────────────────────────────────────────────────
function appendMessage(role, content) {
  const wrap = document.createElement('div');
  wrap.className = `msg ${role}`;
  wrap.innerHTML = `<div class="msg-body">${renderMarkdown(content)}</div>
                    <div class="msg-meta">${role === 'user' ? 'You' : agentSelect.options[agentSelect.selectedIndex].text} · ${now()}</div>`;
  chatMessages.appendChild(wrap);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return wrap;
}

function showTyping(show) {
  let el = $('typingIndicator');
  if (show && !el) {
    el = document.createElement('div');
    el.id = 'typingIndicator';
    el.className = 'typing-indicator';
    el.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
    chatMessages.appendChild(el);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  } else if (!show && el) {
    el.remove();
  }
}

function setStatus(cls, label) {
  statusDot.className = 'status-dot' + (cls ? ` ${cls}` : '');
  statusLabel.textContent = label;
}

function now() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// Minimal markdown renderer: bold, code, code blocks, headers, line breaks
function renderMarkdown(text) {
  if (!text) return '';
  let html = text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    // code blocks
    .replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) =>
      `<div class="code-block-header"><span>${lang||'code'}</span><button class="copy-btn" onclick="copyCode(this)">Copy</button></div><pre><code>${code}</code></pre>`)
    // inline code
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // bold
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // headers
    .replace(/^### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/^# (.+)$/gm, '<h2>$1</h2>')
    // bullets
    .replace(/^[*\-] (.+)$/gm, '<li>$1</li>')
    // newlines
    .replace(/\n/g, '<br>');
  return html;
}

function addCopyButtons(el) {
  el.querySelectorAll('pre code').forEach(block => {
    const btn = block.closest('pre')?.previousSibling;
    if (btn && btn.classList?.contains('code-block-header')) return;
  });
}

function copyCode(btn) {
  const code = btn.closest('.msg-body')?.querySelector('pre code');
  if (code) navigator.clipboard.writeText(code.innerText).then(() => { btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = 'Copy', 1500); });
}

function playAudio(b64) {
  const audio = new Audio(`data:audio/mp3;base64,${b64}`);
  audio.play().catch(() => {});
}

function animateWaveform(active) {
  const bars = waveform?.querySelectorAll('.bar') || [];
  bars.forEach(bar => {
    if (active) {
      const h = 4 + Math.random() * 24;
      bar.style.height = h + 'px';
      bar._interval = setInterval(() => { bar.style.height = (4 + Math.random() * 24) + 'px'; }, 120);
    } else {
      clearInterval(bar._interval);
      bar.style.height = '4px';
    }
  });
}

function autoResizeTextarea() {
  textInput.style.height = 'auto';
  textInput.style.height = Math.min(textInput.scrollHeight, 120) + 'px';
}

// ─── Task log polling ─────────────────────────────────────────────────────────
async function refreshTasks() {
  try {
    const res  = await fetch('/api/tasks');
    const data = await res.json();
    const log  = $('taskLog');
    log.innerHTML = '';
    data.tasks.slice(-10).reverse().forEach(t => {
      const div = document.createElement('div');
      div.className = 'task-item';
      div.innerHTML = `<div class="task-status ${t.status}"></div>
                       <div style="flex:1;overflow:hidden">
                         <div style="font-weight:600">${t.agent_type}</div>
                         <div style="color:var(--text2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${t.prompt_preview}</div>
                       </div>
                       <div style="font-size:10px;color:var(--text2)">${t.status}</div>`;
      log.appendChild(div);
    });
  } catch {}
}

// ─── Draggable popups ─────────────────────────────────────────────────────────
function makeDraggable(popup, handle) {
  let startX, startY, startL, startT;
  handle.addEventListener('mousedown', e => {
    startX = e.clientX; startY = e.clientY;
    const rect = popup.getBoundingClientRect();
    startL = rect.left; startT = rect.top;
    popup.style.right = 'auto'; popup.style.bottom = 'auto';
    popup.style.left = startL + 'px'; popup.style.top = startT + 'px';
    const move = e2 => {
      popup.style.left = (startL + e2.clientX - startX) + 'px';
      popup.style.top  = (startT + e2.clientY - startY) + 'px';
    };
    const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
    e.preventDefault();
  });
}

// ─── Event wiring ─────────────────────────────────────────────────────────────
function init() {
  connectWS();

  // FAB / open chat
  $('fabBtn').onclick = () => { chatPopup.classList.toggle('hidden'); };
  $('btnOpenChat').onclick = () => { chatPopup.classList.toggle('hidden'); };
  $('btnCloseChat').onclick = () => chatPopup.classList.add('hidden');
  $('btnMinimize').onclick = () => {
    const msgs = chatMessages;
    msgs.style.display = msgs.style.display === 'none' ? '' : 'none';
  };

  // Video popup
  $('btnOpenVideo').onclick  = () => videoPopup.classList.toggle('hidden');
  $('btnCloseVideo').onclick = () => { videoPopup.classList.add('hidden'); stopCamera(); };

  // Theme toggle
  $('btnTheme').onclick = () => {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    document.body.classList.toggle('light', state.theme === 'light');
    $('btnTheme').textContent = state.theme === 'dark' ? '🌙' : '☀';
  };

  // Send text
  $('btnSend').onclick = sendTextMessage;
  textInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendTextMessage(); }
  });
  textInput.addEventListener('input', autoResizeTextarea);

  // Mode tabs
  document.querySelectorAll('.mode-tab').forEach(tab => {
    tab.onclick = () => {
      document.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const mode = tab.dataset.mode;
      state.currentMode = mode;
      ['textMode','voiceMode','videoMode'].forEach(id => $( id)?.classList.add('hidden'));
      $(`${mode}Mode`)?.classList.remove('hidden');
    };
  });

  // Voice
  $('btnMic').onclick = () => {
    if (state.isRecording) stopRecording(); else startRecording();
  };

  // In-chat camera controls
  $('btnStartCam').onclick = async () => {
    await startCamera(localVideo);
    $('btnStartCam').disabled = true;
  };
  $('btnSnap').onclick = () => {
    if (!state.cameraStream) { alert('Start camera first'); return; }
    const b64 = captureSnapshot(localVideo);
    setSnapshotPreview(b64);
    switchToTextMode();
  };
  $('btnStopCam').onclick = () => { stopCamera(); $('btnStartCam').disabled = false; };
  $('btnSendSnap').onclick = () => {
    const b64 = state.pendingSnapshotB64 || (state.cameraStream ? captureSnapshot(localVideo) : null);
    if (!b64) { alert('Take a snapshot first'); return; }
    const prompt = $('snapshotPrompt').value.trim() || 'Describe this image.';
    showTyping(true);
    sendWS({ type: 'snapshot', payload: { image_b64: b64, prompt } });
    $('snapshotPrompt').value = '';
  };

  // Clear snapshot
  $('btnClearSnapshot').onclick = clearSnapshot;

  // Live camera popup
  $('btnLiveSnap').onclick = async () => {
    if (!state.cameraStream) await startCamera(liveCamFeed);
    const b64 = captureSnapshot(liveCamFeed);
    const prompt = $('liveSnapPrompt').value.trim() || 'Describe this image.';
    showTyping(true);
    chatPopup.classList.remove('hidden');
    sendWS({ type: 'snapshot', payload: { image_b64: b64, prompt } });
    $('liveSnapPrompt').value = '';
  };

  // Tasks
  $('btnRefreshTasks').onclick = refreshTasks;
  refreshTasks();
  setInterval(refreshTasks, 10000);

  // Memory popup
  $('btnOpenMemory').onclick = () => {
    $('memoryPopup').classList.toggle('hidden');
    if (!$('memoryPopup').classList.contains('hidden')) loadMemories();
  };
  $('btnCloseMemory').onclick = () => $('memoryPopup').classList.add('hidden');
  $('btnMemRefresh').onclick  = () => loadMemories();
  $('btnMemSearch').onclick   = () => loadMemories($('memSearchInput').value.trim());
  $('memSearchInput').addEventListener('keydown', e => { if(e.key==='Enter') loadMemories($('memSearchInput').value.trim()); });
  $('btnMemAdd').onclick      = addMemory;
  $('memAddInput').addEventListener('keydown', e => { if(e.key==='Enter') addMemory(); });
  $('docIngestInput').onchange = async e => {
    for (const f of e.target.files) await ingestDocument(f);
    e.target.value = '';
    chatPopup.classList.remove('hidden');
  };

  // Export & web search in chat header
  $('btnExportChat').onclick = () => {
    const fmt = prompt('Export format: markdown / html / json', 'markdown');
    if (fmt) exportChat(fmt);
  };
  $('btnWebSearch').onclick = quickWebSearch;

  // Workspace popup
  $('btnOpenWorkspace').onclick = () => {
    $('workspacePopup').classList.toggle('hidden');
    if (!$('workspacePopup').classList.contains('hidden')) openWorkspace();
  };
  $('btnCloseWorkspace').onclick = () => $('workspacePopup').classList.add('hidden');
  $('btnWorkspaceRefresh').onclick = () => refreshWorkspaceTree('.');
  $('btnBuild').onclick = startBuild;
  $('btnCloseFileViewer').onclick = () => $('fileViewer').classList.add('hidden');
  $('btnCopyFile').onclick = () => {
    const txt = $('fileViewerContent').textContent;
    navigator.clipboard.writeText(txt).catch(()=>{});
  };
  $('btnSendFileToChat').onclick = () => {
    const path    = $('fileViewer')._currentPath || '';
    const content = $('fileViewerContent').textContent;
    state.pendingFileContent = `\n\n--- File: ${path} ---\n${content}\n---`;
    appendMessage('user', `📎 Attached: ${path}`);
    chatPopup.classList.remove('hidden');
    $('workspacePopup').classList.add('hidden');
  };

  // Workspace file upload
  $('wsUploadInput').onchange = async (e) => {
    for (const f of e.target.files) {
      const form = new FormData();
      form.append('file', f);
      form.append('dest_path', '.');
      await fetch('/api/workspace/upload', { method: 'POST', body: form });
    }
    refreshWorkspaceTree('.');
  };

  // Terminal popup
  $('btnOpenTerminal').onclick = () => {
    $('terminalPopup').classList.toggle('hidden');
    if (!$('terminalPopup').classList.contains('hidden')) {
      connectTerminal();
      populateTermCwd();
      $('termInput').focus();
    }
  };
  $('btnCloseTerminal').onclick = () => $('terminalPopup').classList.add('hidden');
  $('btnClearTerm').onclick = () => { $('termOutput').innerHTML = ''; };
  $('termInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      const cmd = $('termInput').value.trim();
      if (cmd) {
        _termHistory.unshift(cmd);
        _termHistIdx = -1;
        sendTermCmd(cmd);
        $('termInput').value = '';
      }
    } else if (e.key === 'ArrowUp') {
      _termHistIdx = Math.min(_termHistIdx + 1, _termHistory.length - 1);
      $('termInput').value = _termHistory[_termHistIdx] || '';
      e.preventDefault();
    } else if (e.key === 'ArrowDown') {
      _termHistIdx = Math.max(_termHistIdx - 1, -1);
      $('termInput').value = _termHistIdx >= 0 ? _termHistory[_termHistIdx] : '';
      e.preventDefault();
    }
  });

  // Chat file attach
  $('chatFileInput').onchange = async (e) => {
    for (const f of e.target.files) await uploadFileToChat(f);
    e.target.value = '';
  };

  // Drag-to-drop on chat popup
  chatPopup.addEventListener('dragover', e => { e.preventDefault(); $('dropZone').classList.remove('hidden'); });
  chatPopup.addEventListener('dragleave', () => $('dropZone').classList.add('hidden'));
  chatPopup.addEventListener('drop', async e => {
    e.preventDefault();
    $('dropZone').classList.add('hidden');
    for (const f of e.dataTransfer.files) await uploadFileToChat(f);
  });

  // Settings popup
  $('btnOpenSettings').onclick = () => {
    $('settingsPopup').classList.toggle('hidden');
    loadBackendInfo();
    loadSessions();
  };
  $('btnCloseSettings').onclick = () => $('settingsPopup').classList.add('hidden');

  // Model switch
  $('btnSwitchModel').onclick = switchModel;

  // Session controls
  $('btnSaveSession').onclick  = saveSession;
  $('btnNewSession').onclick   = newSession;
  $('btnClearHistory').onclick = () => {
    if (confirm('Clear all chat messages?')) {
      chatMessages.innerHTML = '';
      state.chatHistory = [];
    }
  };

  // Draggable
  makeDraggable(chatPopup,             $('chatPopupHeader'));
  makeDraggable(videoPopup,            $('videoPopupHeader'));
  makeDraggable($('settingsPopup'),    $('settingsPopupHeader'));
  makeDraggable($('workspacePopup'),   $('workspacePopupHeader'));
  makeDraggable($('terminalPopup'),    $('terminalPopupHeader'));
  makeDraggable($('memoryPopup'),      $('memoryPopupHeader'));

  // Load backend info into dashboard stats
  loadBackendInfo();

  // Welcome message
  appendMessage('ai',
    '**Welcome to AI Multi-Agent Admin!**\n\n' +
    'I am your autonomous AI platform with the following agents:\n' +
    '- 🔧 **Admin** — orchestration & task planning\n' +
    '- 💻 **Code** — write, debug & execute code\n' +
    '- 🔍 **Research** — deep knowledge synthesis\n' +
    '- 🧠 **Reasoning** — extended chain-of-thought\n' +
    '- 📊 **ML** — deep learning & model design\n' +
    '- 👁 **Vision** — image & video analysis\n\n' +
    'Use **Text**, **Voice** (🎙), or **Video** (📷) mode. Auto-routing picks the best agent for you.'
  );
}

function switchToTextMode() {
  document.querySelectorAll('.mode-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.mode === 'text');
  });
  $('textMode').classList.remove('hidden');
  $('voiceMode').classList.add('hidden');
  $('videoMode').classList.add('hidden');
  state.currentMode = 'text';
}

// ─── Memory ───────────────────────────────────────────────────────────────────
async function loadMemories(query = '') {
  const list = $('memoryList');
  if (!list) return;
  try {
    const url  = query ? `/api/memory/search?q=${encodeURIComponent(query)}&top_k=20` : '/api/memory';
    const res  = await fetch(url);
    const data = await res.json();
    const items = data.memories || data.results || [];
    list.innerHTML = items.length ? '' : '<div style="color:var(--text2);font-size:12px;padding:8px">No memories yet.</div>';
    items.forEach(m => {
      const div = document.createElement('div');
      div.className = 'memory-item';
      const score = m.score != null ? `<span class="score">${(m.score*100).toFixed(0)}%</span>` : '';
      div.innerHTML = `<div class="mem-text">${m.text.slice(0,180)}</div>
                       <div class="mem-meta">
                         <span>${m.source || 'user'}</span>
                         ${m.tags?.length ? '<span>'+m.tags.join(', ')+'</span>' : ''}
                         ${score}
                         <span class="mem-use" onclick="useMemory(${JSON.stringify(m.text).replace(/</g,'&lt;')})">→ Chat</span>
                         <span class="mem-del" onclick="deleteMemory('${m.id}')">✕</span>
                       </div>`;
      list.appendChild(div);
    });
  } catch (e) {
    if ($('memoryList')) $('memoryList').innerHTML = `<div style="color:var(--accent3);font-size:12px">${e.message}</div>`;
  }
}

async function addMemory() {
  const inp = $('memAddInput');
  const text = inp?.value.trim();
  if (!text) return;
  await fetch('/api/memory', { method:'POST', headers:{'Content-Type':'application/json'},
                                body: JSON.stringify({text, source:'user'}) });
  inp.value = '';
  loadMemories();
}

async function deleteMemory(id) {
  await fetch(`/api/memory/${id}`, { method:'DELETE' });
  loadMemories();
}

function useMemory(text) {
  if (textInput) { textInput.value = text; textInput.focus(); }
  chatPopup.classList.remove('hidden');
  $('memoryPopup').classList.add('hidden');
}

// ─── Export chat ──────────────────────────────────────────────────────────────
async function exportChat(format = 'markdown') {
  if (!state.chatHistory.length) { alert('Nothing to export yet.'); return; }
  const res  = await fetch('/api/export', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ messages: state.chatHistory, format, title: 'AI Chat Session' }),
  });
  const blob = await res.blob();
  const ext  = {markdown:'md', html:'html', json:'json'}[format] || 'txt';
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(blob);
  a.download = `chat_export.${ext}`;
  a.click();
}

// ─── Web search ───────────────────────────────────────────────────────────────
async function quickWebSearch() {
  const query = prompt('Web search query:');
  if (!query) return;
  appendMessage('user', `🌐 Search: ${query}`);
  showTyping(true);
  try {
    const res  = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    const data = await res.json();
    const lines = (data.results||[]).map(r => `**${r.title}**\n${r.snippet}\n${r.url}`).join('\n\n');
    showTyping(false);
    appendMessage('ai', lines || 'No results found.');
  } catch (e) {
    showTyping(false);
    appendMessage('ai', `⚠ Search failed: ${e.message}`);
  }
}

// ─── Document ingest ──────────────────────────────────────────────────────────
async function ingestDocument(file) {
  const toMem  = $('ingestToMemory')?.checked;
  const toWork = $('ingestToWorkspace')?.checked;
  const form   = new FormData();
  form.append('file', file);
  form.append('save_to_memory',    toMem  ? 'true' : 'false');
  form.append('save_to_workspace', toWork ? 'true' : 'false');
  appendMessage('user', `📄 Ingesting: ${file.name}…`);
  showTyping(true);
  try {
    const res  = await fetch('/api/ingest', { method: 'POST', body: form });
    const data = await res.json();
    showTyping(false);
    const summary = `**${file.name}** ingested\n- ${data.chars.toLocaleString()} characters\n` +
      (data.memory_chunks ? `- ${data.memory_chunks} chunks saved to memory\n` : '') +
      (data.workspace_path ? `- Saved to workspace: ${data.workspace_path}\n` : '') +
      `\nPreview:\n\`\`\`\n${data.preview}\n\`\`\``;
    appendMessage('ai', summary);
    if (toMem) loadMemories();
  } catch (e) {
    showTyping(false);
    appendMessage('ai', `⚠ Ingest failed: ${e.message}`);
  }
}

// ─── Workspace ────────────────────────────────────────────────────────────────
let _wsTerminal = null;
let _wsBuild    = null;
let _termHistory = [];
let _termHistIdx = -1;

async function openWorkspace() {
  $('workspacePopup').classList.remove('hidden');
  await refreshWorkspaceTree('.');
  await refreshProjects();
  populateTermCwd();
}

async function refreshWorkspaceTree(path = '.') {
  const tree = $('workspaceTree');
  tree.innerHTML = '<div style="padding:8px;color:var(--text2);font-size:12px">Loading…</div>';
  try {
    const res  = await fetch(`/api/workspace/tree?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    tree.innerHTML = '';
    if (path !== '.') {
      const up = document.createElement('div');
      up.className = 'tree-item';
      up.innerHTML = '<span class="icon">↑</span><span class="name">..</span>';
      up.onclick = () => refreshWorkspaceTree(path.split('/').slice(0,-1).join('/') || '.');
      tree.appendChild(up);
    }
    (data.entries || []).forEach(e => {
      const div = document.createElement('div');
      div.className = 'tree-item';
      div.innerHTML = `<span class="icon">${e.type==='dir'?'📁':'📄'}</span>
                       <span class="name">${e.name}</span>
                       <span class="size">${e.size!=null ? fmtBytes(e.size) : ''}</span>`;
      if (e.type === 'dir') {
        div.onclick = () => refreshWorkspaceTree(`${path}/${e.name}`.replace(/^\.\//,''));
      } else {
        div.onclick = () => openFileViewer(`${path}/${e.name}`.replace(/^\.\//,''));
      }
      tree.appendChild(div);
    });
    if (!data.entries?.length) tree.innerHTML = '<div style="padding:8px;color:var(--text2);font-size:12px">Empty</div>';
  } catch (e) {
    tree.innerHTML = `<div style="padding:8px;color:var(--accent3);font-size:12px">Error: ${e.message}</div>`;
  }
}

async function openFileViewer(path) {
  try {
    const res  = await fetch(`/api/workspace/read?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    $('fileViewerPath').textContent = path;
    $('fileViewerContent').textContent = data.content || '';
    $('fileViewer').classList.remove('hidden');
    $('fileViewer')._currentPath = path;
  } catch {}
}

async function refreshProjects() {
  const list = $('projectList');
  try {
    const res   = await fetch('/api/workspace/projects');
    const data  = await res.json();
    list.innerHTML = '';
    if (!data.projects?.length) { list.innerHTML = '<div style="font-size:11px;color:var(--text2);padding:6px">No projects yet.</div>'; return; }
    data.projects.forEach(p => {
      const div = document.createElement('div');
      div.className = 'project-item';
      div.innerHTML = `<span>📦 ${p.name}</span>
                       <span style="color:var(--text2);font-size:11px">${p.files} files</span>
                       <span class="dl-btn" onclick="downloadProject('${p.name}')">⬇ ZIP</span>`;
      div.querySelector('span:first-child').onclick = () => refreshWorkspaceTree(p.name);
      list.appendChild(div);
    });
  } catch {}
}

function downloadProject(name) {
  const a = document.createElement('a');
  a.href = `/api/workspace/download/${encodeURIComponent(name)}`;
  a.download = `${name}.zip`;
  a.click();
}

function populateTermCwd() {
  const sel = $('termCwd');
  if (!sel) return;
  fetch('/api/workspace/projects').then(r=>r.json()).then(data => {
    sel.innerHTML = '<option value=".">workspace root</option>';
    (data.projects||[]).forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.name; opt.textContent = p.name;
      sel.appendChild(opt);
    });
  }).catch(()=>{});
}

// ─── Build ────────────────────────────────────────────────────────────────────
function startBuild() {
  const desc = $('buildDesc').value.trim();
  const name = $('buildName').value.trim() || undefined;
  if (!desc) return;

  const log = $('buildLog');
  log.innerHTML = '';
  log.classList.remove('hidden');

  if (_wsBuild) { try { _wsBuild.close(); } catch {} }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  _wsBuild = new WebSocket(`${proto}://${location.host}/ws/build`);

  _wsBuild.onopen = () => {
    _wsBuild.send(JSON.stringify({ description: desc, project_name: name }));
    appendBuildLog('info', `Building: ${desc}…`);
  };
  _wsBuild.onmessage = ({ data }) => {
    const ev = JSON.parse(data);
    if (ev.type === 'tool_call') {
      appendBuildLog('tool', `🔧 ${ev.tool}(${JSON.stringify(ev.inputs).slice(0,60)}…)`);
    } else if (ev.type === 'tool_result') {
      const ok = ev.result?.ok !== false;
      appendBuildLog(ok ? 'result' : 'error', ok ? `✓ ${ev.tool}` : `✗ ${ev.tool}: ${ev.result?.error}`);
    } else if (ev.type === 'step_done') {
      appendBuildLog('result', `✅ Step done`);
    } else if (ev.type === 'build_complete') {
      appendBuildLog('result', '🎉 Build complete!');
      refreshWorkspaceTree('.');
      refreshProjects();
      populateTermCwd();
    } else if (ev.type === 'error') {
      appendBuildLog('error', `✗ ${ev.message}`);
    }
  };
  _wsBuild.onerror = () => appendBuildLog('error', 'Build connection error');
}

function appendBuildLog(cls, text) {
  const log = $('buildLog');
  const div = document.createElement('div');
  div.className = `log-${cls}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function fmtBytes(b) {
  if (b < 1024) return `${b}B`;
  if (b < 1048576) return `${(b/1024).toFixed(1)}K`;
  return `${(b/1048576).toFixed(1)}M`;
}

// ─── Terminal ─────────────────────────────────────────────────────────────────
function connectTerminal() {
  if (_wsTerminal?.readyState === WebSocket.OPEN) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  _wsTerminal = new WebSocket(`${proto}://${location.host}/ws/terminal`);
  _wsTerminal.onclose = () => { _wsTerminal = null; };
  _wsTerminal.onmessage = ({ data }) => {
    const ev = JSON.parse(data);
    const out = $('termOutput');
    const line = document.createElement('div');
    if (ev.type === 'cmd')    { line.className = 'term-line cmd';    line.textContent = ev.text; }
    else if (ev.type === 'output') { line.className = 'term-line';   line.textContent = ev.text; }
    else if (ev.type === 'error')  { line.className = 'term-line error'; line.textContent = `✗ ${ev.text}`; }
    else if (ev.type === 'exit')   { line.className = `term-line exit-${ev.code}`; line.textContent = `[exit ${ev.code}]`; }
    out.appendChild(line);
    out.scrollTop = out.scrollHeight;
  };
}

function sendTermCmd(cmd) {
  const cwd = $('termCwd')?.value || '.';
  if (!_wsTerminal || _wsTerminal.readyState !== WebSocket.OPEN) {
    connectTerminal();
    setTimeout(() => sendTermCmd(cmd), 300);
    return;
  }
  _wsTerminal.send(JSON.stringify({ cmd, cwd }));
}

// ─── File upload to chat ──────────────────────────────────────────────────────
async function uploadFileToChat(file) {
  const form = new FormData();
  form.append('file', file);
  try {
    const res  = await fetch('/api/upload/chat', { method: 'POST', body: form });
    const data = await res.json();
    if (data.type === 'image') {
      setSnapshotPreview(data.b64);
      appendMessage('user', `📎 Attached image: ${data.filename}`);
    } else {
      state.pendingFileContent = `\n\n--- File: ${data.filename} ---\n${data.content}\n---`;
      appendMessage('user', `📎 Attached: ${data.filename} (${fmtBytes(data.size || 0)})`);
    }
  } catch (e) {
    appendMessage('ai', `⚠ Upload failed: ${e.message}`);
  }
}

// ─── Backend / model info ─────────────────────────────────────────────────────
async function loadBackendInfo() {
  try {
    const res  = await fetch('/api/backend');
    const data = await res.json();

    // Dashboard stats
    const statBackend = $('statBackend');
    const statModel   = $('statModel');
    if (statBackend) statBackend.textContent = data.backend === 'foundry_local' ? 'Foundry Local' : 'Anthropic Claude';
    if (statModel)   statModel.textContent   = data.active_model || '—';

    // Settings panel badge
    const badge = $('badgeBackend');
    if (badge) {
      const cls = data.backend === 'foundry_local' ? 'foundry' : (data.status === 'ok' ? 'anthropic' : 'error');
      badge.className = `badge ${cls}`;
      badge.textContent = data.backend === 'foundry_local'
        ? `⚡ Foundry Local — ${data.url || ''}`
        : `☁ Anthropic Claude API`;
    }

    // Populate model select
    const sel = $('modelSelect');
    if (sel) {
      sel.innerHTML = '';
      const models = Array.isArray(data.available_models)
        ? data.available_models
        : (data.available_models || []).map(m => m.id || m);
      const active = data.active_model || '';
      models.forEach(m => {
        const id = typeof m === 'string' ? m : m.id;
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = id;
        if (id === active) opt.selected = true;
        sel.appendChild(opt);
      });
      if (!models.length) {
        const opt = document.createElement('option');
        opt.textContent = active || 'default';
        opt.value = active;
        sel.appendChild(opt);
      }
    }
  } catch (e) {
    const badge = $('badgeBackend');
    if (badge) { badge.className = 'badge error'; badge.textContent = 'Backend unreachable'; }
  }
}

async function switchModel() {
  const sel    = $('modelSelect');
  const status = $('switchStatus');
  if (!sel) return;
  const model = sel.value;
  status.textContent = `Switching to ${model}…`;
  status.style.color = 'var(--text2)';
  try {
    const res  = await fetch('/api/switch-model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });
    const data = await res.json();
    if (res.ok) {
      status.textContent = `✓ Switched to ${data.active_model}`;
      status.style.color = 'var(--green)';
      const statModel = $('statModel');
      if (statModel) statModel.textContent = data.active_model;
    } else {
      status.textContent = `✗ ${data.detail}`;
      status.style.color = 'var(--accent3)';
    }
  } catch (e) {
    status.textContent = `✗ Error: ${e.message}`;
    status.style.color = 'var(--accent3)';
  }
}

// ─── Session management ───────────────────────────────────────────────────────
function sessionId() {
  if (!state.sessionId) state.sessionId = `sess_${Date.now()}`;
  return state.sessionId;
}

async function saveSession() {
  const id = sessionId();
  try {
    await fetch(`/api/sessions/${id}/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state.chatHistory),
    });
    $('switchStatus') && ($('switchStatus').textContent = '✓ Session saved');
    loadSessions();
  } catch (e) {}
}

function newSession() {
  state.sessionId = `sess_${Date.now()}`;
  chatMessages.innerHTML = '';
  state.chatHistory = [];
}

async function loadSessions() {
  const list = $('sessionList');
  if (!list) return;
  try {
    const res  = await fetch('/api/sessions');
    const data = await res.json();
    list.innerHTML = '';
    data.sessions.forEach(s => {
      const div = document.createElement('div');
      div.className = 'session-item';
      div.innerHTML = `<span>${s.id.replace('sess_', '#')}</span><span class="turns">${s.turns} turns</span>`;
      div.onclick = () => loadSessionHistory(s.id);
      list.appendChild(div);
    });
  } catch {}
}

async function loadSessionHistory(id) {
  try {
    const res  = await fetch(`/api/sessions/${id}`);
    const data = await res.json();
    state.chatHistory = data.messages || [];
    state.sessionId   = id;
    chatMessages.innerHTML = '';
    state.chatHistory.forEach(m => appendMessage(m.role === 'user' ? 'user' : 'ai', m.content));
    $('settingsPopup').classList.add('hidden');
    chatPopup.classList.remove('hidden');
  } catch {}
}

window.addEventListener('DOMContentLoaded', init);
window.copyCode         = copyCode;
window.downloadProject  = downloadProject;
window.deleteMemory     = deleteMemory;
window.useMemory        = useMemory;
