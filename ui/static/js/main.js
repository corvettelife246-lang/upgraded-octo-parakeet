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
  currentMode: 'text',
  theme: 'dark',
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
  const payload = {
    message: text,
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

  // Draggable
  makeDraggable(chatPopup,  $('chatPopupHeader'));
  makeDraggable(videoPopup, $('videoPopupHeader'));

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

window.addEventListener('DOMContentLoaded', init);
window.copyCode = copyCode;
