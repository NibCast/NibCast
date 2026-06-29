// ============================================================
//  NibCast — Dashboard JS  v9
// ============================================================

// ── State ───────────────────────────────────────────────────
let _history = [];
let _filteredHistory = [];
let _targetRules = {};
let _hotkeys = [{ value: '<ctrl>+<alt>+v', mode: 'preset', recordMode: 'hold' }];
let _hasChanges = false;
let _currentTheme = 'foundry';
let _currentAccent = 'amber';
let _widgetStyle = 'wave';
let _toastTimer        = null;
let _lightMode         = false;
let _updateToastShown  = false;

// Toggle state map: DOM id → config key
const TOGGLE_MAP = {
  togCleanLlm:          'CLEAN_WITH_LLM',
  togAppendNl:          'APPEND_NEWLINE',
  togPreserveClip:      'PRESERVE_CLIPBOARD',
  togEditBefore:        'EDIT_BEFORE_PASTE',
  togAudioCues:         'AUDIO_CUES',
  togAudioCueStart:     'AUDIO_CUE_START',
  togAudioCueStop:      'AUDIO_CUE_STOP',
  togAudioCueError:     'AUDIO_CUE_ERROR',
  togBrainMode:         'BRAIN_MODE',
  togStartMinimized:    'START_MINIMIZED',
  togShowWidgetOnStart: 'SHOW_WIDGET_ON_START',
  togPrivacyMode:       'PRIVACY_MODE',
  togContextAwareness:  'CONTEXT_AWARENESS',
  togDeepgramDiarize:   'DEEPGRAM_DIARIZE',
  togVoiceEnroll:       'VOICE_ENROLLMENT_ENABLED',
};

const _cfgToggles = {
  CLEAN_WITH_LLM:     true,
  APPEND_NEWLINE:     false,
  PRESERVE_CLIPBOARD: true,
  EDIT_BEFORE_PASTE:  false,
  AUDIO_CUES:         true,
  AUDIO_CUE_START:    true,
  AUDIO_CUE_STOP:     true,
  AUDIO_CUE_ERROR:    true,
  WAKE_WORD_ENABLED:  false,
  BRAIN_MODE:         false,
  START_MINIMIZED:    false,
  SHOW_WIDGET_ON_START: true,
  PRIVACY_MODE:       false,
  CONTEXT_AWARENESS:  true,
  DEEPGRAM_DIARIZE:   false,
  VOICE_ENROLLMENT_ENABLED: true,
};

let _asrBackend = 'nvidia';
let _llmBackend = 'nvidia';
let _recordingMode = 'hold';

const HOTKEY_PRESETS = [
  { label: 'Ctrl+Alt+V',        value: '<ctrl>+<alt>+v'         },
  { label: 'Ctrl+Alt+Space',    value: '<ctrl>+<alt>+<space>'   },
  { label: 'Scroll Lock',       value: '<scroll_lock>'          },
  { label: 'F9',                value: '<f9>'                   },
  { label: 'F10',               value: '<f10>'                  },
  { label: 'Ctrl+Shift+Space',  value: '<ctrl>+<shift>+<space>' },
  { label: 'Alt+Space',         value: '<alt>+<space>'          },
];

const ACCENT_COLORS = {
  amber:  { pri:'#d4a742', dim:'#8c6e2a', bg:'rgba(212,167,66,0.08)',  glow:'rgba(212,167,66,0.2)',  name:'Amber'  },
  coral:  { pri:'#d46e5a', dim:'#8c4a3a', bg:'rgba(212,110,90,0.08)',  glow:'rgba(212,110,90,0.2)',  name:'Coral'  },
  cyan:   { pri:'#5ab8d4', dim:'#3a7a8a', bg:'rgba(90,184,212,0.08)',  glow:'rgba(90,184,212,0.2)',  name:'Cyan'   },
  purple: { pri:'#9b7ad4', dim:'#6a4a9a', bg:'rgba(155,122,212,0.08)', glow:'rgba(155,122,212,0.2)', name:'Purple' },
};

const FONTS_UI = {
  'dm-mono':    "'DM Mono', monospace",
  'ibm-plex':   "'IBM Plex Mono', monospace",
  'space-mono': "'Space Mono', monospace",
  'system-mono':"ui-monospace, 'Consolas', 'Courier New', monospace",
};

const FONTS_DISPLAY = {
  'cormorant': "'Cormorant Garamond', serif",
  'serif':     "Georgia, 'Times New Roman', serif",
  'mono':      "var(--vf-font-b)",
};

// ── Init ────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  restoreAppearance();
  initWaveform();
  renderHotkeyBuilder();
  refreshAll();
  loadDevices();
  checkSetupStatus();
  setInterval(refreshAll, 30000);
});

// ── First-run setup banner + wizard ─────────────────────────
let _wizStep = 1;
const _WIZ_SEEN_KEY = 'nibcast_wizard_seen';

function checkSetupStatus() {
  fetch('/api/setup-status')
    .then(r => r.json())
    .then(d => {
      if (d.missing_keys && d.missing_keys.length > 0) {
        const m = d.missing_keys[0];
        const banner = document.getElementById('setup-banner');
        const msg    = document.getElementById('setup-banner-msg');
        if (banner && msg) {
          msg.textContent = `API key not configured — voice dictation won't work until you add it (${m.key}).`;
          banner.style.display = 'flex';
        }
        // Show full wizard only on first ever visit
        if (!localStorage.getItem(_WIZ_SEEN_KEY)) {
          wizShow();
        }
      }
    })
    .catch(() => {});
}

function wizShow() {
  const el = document.getElementById('wizard-overlay');
  if (el) el.style.display = 'flex';
  _wizStep = 1;
  wizRender();
}

function wizClose() {
  const el = document.getElementById('wizard-overlay');
  if (el) el.style.display = 'none';
  localStorage.setItem(_WIZ_SEEN_KEY, '1');
}

function wizRender() {
  [1,2,3].forEach(i => {
    const s = document.getElementById('wiz-step-' + i);
    if (s) s.style.display = i === _wizStep ? '' : 'none';
    const d = document.getElementById('wiz-dot-' + i);
    if (d) d.style.background = i <= _wizStep ? '#e8a525' : '#333';
  });
  const back = document.getElementById('wiz-back');
  const next = document.getElementById('wiz-next');
  const skip = document.getElementById('wiz-skip');
  if (back) back.style.display = _wizStep > 1 ? '' : 'none';
  if (next) next.textContent = _wizStep === 3 ? 'GET STARTED ✓' : 'NEXT →';
  if (skip) skip.style.display = _wizStep === 3 ? 'none' : '';
}

function wizNext() {
  if (_wizStep === 3) { wizClose(); return; }
  _wizStep++;
  wizRender();
}
function wizBack() {
  if (_wizStep > 1) { _wizStep--; wizRender(); }
}

function wizKeyChanged() {
  const btn = document.getElementById('wiz-save-btn');
  const val = (document.getElementById('wiz-api-key') || {}).value || '';
  if (btn) btn.disabled = val.trim().length < 20;
}

function wizSaveKey() {
  const val = ((document.getElementById('wiz-api-key') || {}).value || '').trim();
  const status = document.getElementById('wiz-key-status');
  if (!val) return;
  if (status) { status.textContent = 'Saving…'; status.style.color = '#888'; }
  fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ GROQ_API_KEY: val, ASR_BACKEND: 'groq', LLM_BACKEND: 'groq' })
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok || d.saved) {
      if (status) { status.textContent = '✓ Saved! Testing connection…'; status.style.color = '#25ffe0'; }
      // Hide banner
      const banner = document.getElementById('setup-banner');
      if (banner) banner.style.display = 'none';
      setTimeout(wizNext, 900);
    } else {
      if (status) { status.textContent = '✕ Save failed — try again'; status.style.color = '#ff3838'; }
    }
  })
  .catch(() => {
    if (status) { status.textContent = '✕ Connection error'; status.style.color = '#ff3838'; }
  });
}

// ── Panel Switching ──────────────────────────────────────────
function switchPanel(id) {
  document.querySelectorAll('.vf-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.vf-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.vf-sb-btn[data-panel]').forEach(b => b.classList.remove('active'));

  const panel = document.getElementById('panel-' + id);
  if (panel) panel.classList.add('active');

  document.querySelectorAll(`.vf-tab[data-panel="${id}"], .vf-sb-btn[data-panel="${id}"]`)
    .forEach(el => el.classList.add('active'));

  if (id === 'insights') { renderInsights(); loadUsageStats(); }
}

// ── Native window controls ───────────────────────────────────
// Wired to the pywebview js_api bridge exposed by desktop_app.py.
// These buttons only become visible once `pywebviewready` fires
// (i.e. we're inside the frameless native window). Under the
// Chrome --app launch the bridge is absent and Chrome's own
// titlebar provides minimize/maximize/close instead.
function winMinimize() { try { window.pywebview && pywebview.api.minimize(); } catch (e) {} }
function winMaximize() { try { window.pywebview && pywebview.api.toggle_maximize(); } catch (e) {} }
function winClose()    { try { window.pywebview ? pywebview.api.close() : window.close(); } catch (e) { try { window.close(); } catch (_) {} } }

window.addEventListener('pywebviewready', () => {
  document.documentElement.classList.add('is-native');
  const c = document.getElementById('winCtl');
  if (c) c.style.display = 'flex';
});

// ── Toast ────────────────────────────────────────────────────
function showToast(msg) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 2500);
}

// ── Waveform ─────────────────────────────────────────────────
function initWaveform() {
  const wf = document.getElementById('waveform');
  if (!wf) return;
  wf.innerHTML = '';
  for (let i = 0; i < 28; i++) {
    const wv = document.createElement('div');
    wv.className = 'wv';
    wv.style.animationDelay = (i * 0.07) + 's';
    wf.appendChild(wv);
  }
}

// ── Helpers ──────────────────────────────────────────────────
function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = String(s || '');
  return d.innerHTML;
}

function _debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function fmtTime(ts) {
  if (!ts) return '—';
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.round(diff / 60) + 'm ago';
  if (diff < 86400) return Math.round(diff / 3600) + 'h ago';
  return new Date(ts).toLocaleDateString();
}

function fmtExact(ts) {
  if (!ts) return { time: '—', date: '' };
  const d = new Date(ts);
  return {
    time: d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    date: d.toLocaleDateString([], { month: 'short', day: 'numeric' }),
  };
}

function wordCount(text) {
  if (!text) return 0;
  return text.split(/\s+/).filter(Boolean).length;
}

function highlightSearch(html, search) {
  if (!search) return html;
  const escaped = search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return html.replace(new RegExp(escaped, 'gi'), m =>
    `<mark>${m}</mark>`);
}

function setSelectValue(id, val) {
  const el = document.getElementById(id);
  if (!el) return;
  for (const opt of el.options) {
    if (opt.value === String(val)) { opt.selected = true; return; }
  }
}

// ── Load Stats ───────────────────────────────────────────────
function loadStats() {
  fetch('/api/stats')
    .then(r => r.json())
    .then(d => {
      const total = d.total_sessions || 0;
      const today = d.today_sessions || 0;
      const words = d.total_words || 0;
      const avg   = d.avg_duration || 0;

      const el = id => document.getElementById(id);
      if (el('statTotal')) el('statTotal').textContent = total;
      if (el('statToday')) el('statToday').textContent = today;
      if (el('statWords')) el('statWords').textContent = words > 999 ? (words / 1000).toFixed(1) + 'k' : words;
      if (el('statAvg'))   el('statAvg').textContent   = avg.toFixed(1) + 's';
      if (el('sbDbRows'))  el('sbDbRows').textContent  = total + ' rows';
      if (el('sbLogCount')) el('sbLogCount').textContent = total;
    })
    .catch(() => {});
}

// ── Load History ─────────────────────────────────────────────
function loadHistory() {
  fetch('/api/history')
    .then(r => r.json())
    .then(d => {
      _history = Array.isArray(d) ? d : [];
      renderRecentList();
      filterLog();
      const countEl = document.getElementById('sbLogCount');
      if (countEl) countEl.textContent = _history.length;
    })
    .catch(() => {});
}

function renderRecentList() {
  const el = document.getElementById('recentList');
  if (!el) return;
  const recent = _history.slice(0, 8);

  const badge = document.getElementById('recentCountBadge');
  if (badge) badge.textContent = _history.length ? `(${_history.length} total)` : '';

  if (!recent.length) {
    el.innerHTML = '<div class="empty">No transcriptions yet</div>';
    return;
  }

  el.innerHTML = recent.map(item => {
    const cat   = item.category || 'generic';
    const tsMs  = item.created_at ? new Date(item.created_at).getTime() : 0;
    const dur   = item.duration_sec || 0;
    const app   = item.target_app || 'Unknown';
    const text  = item.clean_text || item.raw_text || '';
    const words = wordCount(text);

    const dt = fmtExact(tsMs);
    const appShort = app.length > 22 ? app.slice(0, 20) + '…' : app;
    const textEsc = escHtml(text);

    const quality = _qualityScore(item.raw_text || '', text);
    const qlColor = quality >= 90 ? 'var(--vf-cyan)' : quality >= 70 ? 'var(--vf-pri)' : 'var(--vf-muted)';
    const itemId  = item.id;

    return `<div class="recent-item" onclick="this.classList.toggle('expanded')">
      <div class="recent-item-hdr">
        <span class="tag ${escHtml(cat)}">${escHtml(cat).toUpperCase()}</span>
        <span class="recent-time-badge">${escHtml(dt.time)}</span>
        <span style="font-size:9px;letter-spacing:1px;color:${qlColor};margin-left:auto">${quality}</span>
      </div>
      <div class="recent-text">${textEsc || '<span style="opacity:.4;font-style:italic">empty transcript</span>'}</div>
      <div class="recent-meta">
        <span class="rmeta">${escHtml(dt.date)}</span>
        <span class="rmeta">${escHtml(appShort)}</span>
        <span class="rmeta">${dur.toFixed(1)}s</span>
        <span class="rmeta words">${words} words</span>
        <span class="rmeta" style="margin-left:auto;display:flex;gap:4px" onclick="event.stopPropagation()">
          <button class="copy-btn" onclick="copyEntry(${itemId})" title="Copy to clipboard">⎘</button>
          <button class="down-btn" onclick="downloadEntry(${itemId})" title="Download as .txt">↓</button>
        </span>
      </div>
    </div>`;
  }).join('');
}

// ── Log Panel ────────────────────────────────────────────────
function filterLog() {
  const search = (document.getElementById('logSearch')?.value || '').toLowerCase();
  const cat    = document.getElementById('logCatFilter')?.value || '';

  _filteredHistory = _history.filter(x => {
    const text    = (x.clean_text || x.raw_text || '').toLowerCase();
    const app     = (x.target_app || '').toLowerCase();
    const xCat    = x.category || 'generic';
    const xStatus = x.status   || 'success';
    const matchS  = !search || text.includes(search) || app.includes(search);
    const matchC  = !cat || xCat === cat;
    // Hide system/discarded entries unless user explicitly enables them
    const matchStatus = _showDiscarded || !_HIDDEN_STATUSES.has(xStatus);
    return matchS && matchC && matchStatus;
  });

  renderLogTable();
}
const filterLogDebounced = _debounce(filterLog, 250);

let _showRaw      = false;
let _showScore    = false;
let _showDiscarded = false;   // hidden by default: discarded/empty/wake_word entries

// Statuses that are shown only when user opts in
const _HIDDEN_STATUSES = new Set(['discarded', 'empty', 'wake_word', 'cancelled']);

function toggleShowRaw() {
  _showRaw = !_showRaw;
  const btn = document.getElementById('toggleRawBtn');
  if (btn) btn.textContent = _showRaw ? 'SHOW CLEAN' : 'SHOW RAW';
  renderLogTable();
}

function toggleFidelityScore() {
  _showScore = !_showScore;
  const btn = document.getElementById('toggleScoreBtn');
  if (btn) btn.classList.toggle('active', _showScore);
  if (btn) btn.textContent = _showScore ? 'HIDE SCORE' : 'SHOW SCORE';
  renderLogTable();
}

function toggleDiscarded() {
  _showDiscarded = !_showDiscarded;
  const btn = document.getElementById('toggleDiscardedBtn');
  if (btn) btn.classList.toggle('active', _showDiscarded);
  if (btn) btn.textContent = _showDiscarded ? 'SUCCESS ONLY' : 'SHOW ALL';
  filterLog();
}

/**
 * Fidelity score: how closely does clean_text preserve what Whisper heard (raw_text)?
 * 100 = identical, 0 = completely rewritten.
 * Uses Jaccard word-set overlap after normalising both texts.
 * Returns null when there's nothing meaningful to compare.
 */
function _fidelityScore(raw, clean) {
  if (!raw || !clean) return null;
  const norm = s => s.toLowerCase().replace(/[^\w\s]/g, '').split(/\s+/).filter(Boolean);
  const rw = norm(raw), cw = norm(clean);
  if (!rw.length || !cw.length) return null;
  if (raw.trim().toLowerCase() === clean.trim().toLowerCase()) return 100;
  const rs = new Set(rw), cs = new Set(cw);
  const inter = [...rs].filter(w => cs.has(w)).length;
  const union  = new Set([...rs, ...cs]).size;
  return union > 0 ? Math.round(100 * inter / union) : 100;
}

function renderLogTable() {
  const tbody   = document.getElementById('logBody');
  const countEl = document.getElementById('logCount');
  if (countEl) countEl.textContent = _filteredHistory.length + ' entries';
  if (!tbody) return;

  const search = (document.getElementById('logSearch')?.value || '').toLowerCase();

  if (!_filteredHistory.length) {
    tbody.innerHTML = '<tr><td colspan="6"><div class="empty" style="border:none">No entries found</div></td></tr>';
    return;
  }

  tbody.innerHTML = _filteredHistory.map(x => {
    const cat  = x.category || 'generic';
    const tsMs = x.created_at ? new Date(x.created_at).getTime() : 0;
    const dur  = x.duration_sec || 0;
    // Show raw or clean based on toggle; dim indicator when showing raw
    const rawText   = x.raw_text   || '';
    const cleanText = x.clean_text || rawText;
    const text      = _showRaw ? rawText : cleanText;
    const textLabel = (_showRaw && rawText && rawText !== cleanText)
      ? `<span style="font-size:8px;color:var(--vf-muted);letter-spacing:1px;margin-right:4px">[RAW]</span>`
      : '';
    const app  = x.target_app || '';
    const id   = x.id;
    const dt   = fmtExact(tsMs);
    const appShort = app.length > 18 ? app.slice(0, 16) + '…' : app;
    const textHtml = textLabel + highlightSearch(escHtml(text), search);
    const status = x.status || 'success';
    const isProblematic = status !== 'success';
    const statusBadge = isProblematic
      ? `<span style="font-size:8px;letter-spacing:1px;color:#f87;background:rgba(255,80,80,0.1);border:1px solid rgba(255,80,80,0.25);border-radius:2px;padding:1px 5px;margin-right:6px;vertical-align:middle">${escHtml(status.toUpperCase())}</span>`
      : '';
    const pasteText = cleanText;
    const pasteBtn = pasteText
      ? `<button class="copy-btn" onclick="pasteEntry(${id})" title="Paste into active window" style="color:var(--vf-cyan)">⏎</button>`
      : '';

    // Fidelity badge (optional — shown when SHOW SCORE is toggled)
    let fidelityBadge = '';
    if (_showScore && rawText && cleanText && rawText !== cleanText) {
      const fs = _fidelityScore(rawText, cleanText);
      if (fs !== null) {
        const fc = fs >= 85 ? '#25ffe0' : fs >= 60 ? '#e8a525' : '#ff3838';
        fidelityBadge = `<span title="Speech fidelity: how closely the injected text matches what Whisper heard"
          style="font-size:8px;letter-spacing:1px;color:${fc};border:1px solid ${fc};
          border-radius:2px;padding:1px 5px;margin-right:6px;vertical-align:middle;
          opacity:0.85">${fs}%</span>`;
      }
    }

    return `<tr>
      <td class="log-time">${escHtml(dt.time)}<div class="log-date">${escHtml(dt.date)}</div></td>
      <td class="log-app" title="${escHtml(app)}">${escHtml(appShort)}</td>
      <td class="log-dur">${dur.toFixed(1)}s</td>
      <td><span class="tag ${escHtml(cat)}">${escHtml(cat).toUpperCase()}</span></td>
      <td class="log-txt">${statusBadge}${fidelityBadge}${textHtml}</td>
      <td style="white-space:nowrap">
        ${pasteBtn}
        <button class="copy-btn"  onclick="copyEntry(${id})"    title="Copy to clipboard">⎘</button>
        <button class="down-btn"  onclick="downloadEntry(${id})" title="Download as .txt">↓</button>
        <button class="del-btn"   onclick="deleteEntry(${id})"   title="Delete">✕</button>
      </td>
    </tr>`;
  }).join('');
}

function _fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px;opacity:0;';
  document.body.appendChild(ta);
  ta.select();
  ta.setSelectionRange(0, 99999);
  try { document.execCommand('copy'); showToast('COPIED'); }
  catch(e) { showToast('COPY FAILED — try Ctrl+C'); }
  document.body.removeChild(ta);
}

function copyText(text) {
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text)
      .then(() => showToast('COPIED'))
      .catch(() => _fallbackCopy(text));
  } else {
    _fallbackCopy(text);
  }
}

// ID-based copy/download — safe for any text content
function copyEntry(id) {
  const item = _history.find(x => x.id === id);
  if (!item) { showToast('ENTRY NOT FOUND'); return; }
  const text = item.clean_text || item.raw_text || '';
  if (!text) { showToast('NOTHING TO COPY'); return; }
  copyText(text);
}

function downloadEntry(id) {
  const item = _history.find(x => x.id === id);
  if (!item) return;
  const text = item.clean_text || item.raw_text || '';
  const ts = item.created_at
    ? new Date(item.created_at).toISOString().slice(0, 19).replace(/[T:]/g, '-')
    : String(Date.now());
  downloadTxt(text, ts);
}

function downloadTxt(text, label) {
  const blob = new Blob([text], { type: 'text/plain' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = 'transcript_' + (label || Date.now()).toString().replace(/[^a-z0-9_\-]/gi, '_') + '.txt';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);
  showToast('TRANSCRIPT DOWNLOADED');
}

function pasteEntry(id) {
  const item = _history.find(x => x.id === id);
  if (!item) { showToast('ENTRY NOT FOUND'); return; }
  const text = item.clean_text || item.raw_text || '';
  if (!text) { showToast('NOTHING TO PASTE'); return; }
  fetch('/api/inject', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) showToast('PASTED — ' + text.slice(0, 30) + (text.length > 30 ? '…' : ''));
      else showToast('PASTE FAILED: ' + (d.error || 'unknown'));
    })
    .catch(() => showToast('PASTE FAILED'));
}

function deleteEntry(id) {
  if (!id) return;
  fetch('/api/history/' + id, { method: 'DELETE' })
    .then(r => r.json())
    .then(() => {
      _history = _history.filter(x => x.id !== id);
      renderRecentList();
      filterLog();
      showToast('ENTRY DELETED');
    })
    .catch(() => showToast('DELETE FAILED'));
}

function exportCSV() {
  window.location.href = '/api/history/export';
  showToast('EXPORT STARTED');
}

// PDF export needs PyMuPDF, which isn't bundled with NibCast (it's AGPL-3.0
// licensed — shipping it in the .exe would require distributing NibCast
// itself under AGPL). So on a fresh install the request can 500 with
// {"ok": false, "error": "PyMuPDF not installed. Run: pip install pymupdf"}.
// Fetch first so we can catch that and tell the user to install it from a
// terminal, instead of navigating to a raw JSON/error page.
function exportPDF(devMode) {
  const url = '/api/history/export-pdf' + (devMode ? '?mode=dev' : '');
  showToast('GENERATING PDF…');

  fetch(url)
    .then(async r => {
      const ctype = r.headers.get('content-type') || '';
      if (!r.ok || ctype.includes('application/json')) {
        const d = await r.json().catch(() => ({}));
        if ((d.error || '').toLowerCase().includes('pymupdf')) {
          showToast('PDF EXPORT NEEDS AN EXTRA PACKAGE — SEE BELOW');
          alert(
            'PDF export requires PyMuPDF, which NibCast does not bundle ' +
            '(it is AGPL-3.0 licensed, so it ships separately to keep NibCast under MIT).\n\n' +
            'To enable PDF export, open a terminal in your NibCast folder and run:\n\n' +
            '    pip install pymupdf\n\n' +
            'Then restart NibCast and try again. (CSV export works without this.)'
          );
        } else {
          showToast('PDF EXPORT FAILED: ' + (d.error || 'unknown error').substring(0, 60));
        }
        return;
      }
      const blob = await r.blob();
      const disposition = r.headers.get('content-disposition') || '';
      const match = disposition.match(/filename=([^;]+)/);
      const fname = match ? match[1].trim() : (devMode ? 'nibcast_dev_export.pdf' : 'nibcast_history.pdf');
      const a = document.createElement('a');
      const objUrl = URL.createObjectURL(blob);
      a.href = objUrl;
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objUrl);
      showToast('PDF DOWNLOADED');
    })
    .catch(() => showToast('PDF EXPORT FAILED — CONNECTION ERROR'));
}

function clearLog() {
  if (!confirm('Clear all history? This cannot be undone.')) return;
  fetch('/api/history/clear', { method: 'POST' })
    .then(() => {
      _history = [];
      renderRecentList();
      filterLog();
      const countEl = document.getElementById('sbLogCount');
      if (countEl) countEl.textContent = '0';
      showToast('LOG CLEARED');
    })
    .catch(() => showToast('CLEAR FAILED'));
}

// ── Insights Panel ───────────────────────────────────────────
function renderInsights() {
  if (!_history.length) return;

  // Words per session
  const wordsArr = _history.map(x => wordCount(x.clean_text || x.raw_text || ''));
  const avgWords = Math.round(wordsArr.reduce((a, b) => a + b, 0) / wordsArr.length);
  const el = id => document.getElementById(id);
  if (el('insWordsPerSession')) el('insWordsPerSession').textContent = avgWords;

  // Most used app
  const appCounts = {};
  _history.forEach(x => {
    const a = x.target_app || 'Unknown';
    appCounts[a] = (appCounts[a] || 0) + 1;
  });
  const topApp = Object.entries(appCounts).sort((a, b) => b[1] - a[1])[0];
  if (el('insMostApp') && topApp) {
    const name = topApp[0].length > 14 ? topApp[0].slice(0, 12) + '…' : topApp[0];
    el('insMostApp').textContent = name;
  }

  // Peak hour
  const hourCounts = new Array(24).fill(0);
  _history.forEach(x => {
    if (x.created_at) {
      const h = new Date(x.created_at).getHours();
      hourCounts[h]++;
    }
  });
  const peakH = hourCounts.indexOf(Math.max(...hourCounts));
  if (el('insPeakHour')) {
    const label = peakH === 0 ? '12am' : peakH < 12 ? `${peakH}am` : peakH === 12 ? '12pm' : `${peakH-12}pm`;
    el('insPeakHour').textContent = label;
  }

  // Success rate
  const withText = _history.filter(x => (x.clean_text || x.raw_text || '').trim().length > 0).length;
  const rate = Math.round((withText / _history.length) * 100);
  if (el('insSuccessRate')) el('insSuccessRate').textContent = rate + '%';

  // Heatmap
  const hm = document.getElementById('heatmapCells');
  if (hm) {
    const maxV = Math.max(...hourCounts, 1);
    hm.innerHTML = hourCounts.map((v, h) => {
      const intensity = Math.round((v / maxV) * 5);
      const label = h === 0 ? '12am' : h < 12 ? `${h}am` : h === 12 ? '12pm' : `${h-12}pm`;
      return `<div class="hm-cell hm-${intensity}" title="${label}: ${v} sessions"></div>`;
    }).join('');
  }

  // Apps bar list
  const abl = document.getElementById('appsBarList');
  if (abl) {
    const sorted = Object.entries(appCounts).sort((a, b) => b[1] - a[1]).slice(0, 8);
    const maxC = sorted[0]?.[1] || 1;
    if (!sorted.length) {
      abl.innerHTML = '<div class="empty">No data yet</div>';
    } else {
      abl.innerHTML = sorted.map(([app, count]) => {
        const pct = Math.round((count / maxC) * 100);
        return `<div class="app-bar-row">
          <div class="app-bar-name">${escHtml(app.length > 20 ? app.slice(0,18)+'…' : app)}</div>
          <div class="app-bar-track">
            <div class="app-bar-fill" style="width:${pct}%"></div>
          </div>
          <div class="app-bar-count">${count}</div>
        </div>`;
      }).join('');
    }
  }

  // Category distribution
  const catCounts = {};
  _history.forEach(x => { const c = x.category || 'generic'; catCounts[c] = (catCounts[c]||0)+1; });
  const catEl = document.getElementById('catDistList');
  if (catEl) {
    const sorted = Object.entries(catCounts).sort((a,b)=>b[1]-a[1]);
    const maxC = sorted[0]?.[1] || 1;
    catEl.innerHTML = sorted.map(([cat, count]) => {
      const pct = Math.round((count/maxC)*100);
      return `<div class="app-bar-row">
        <div class="app-bar-name"><span class="tag ${escHtml(cat)}">${escHtml(cat.toUpperCase())}</span></div>
        <div class="app-bar-track"><div class="app-bar-fill" style="width:${pct}%"></div></div>
        <div class="app-bar-count">${count}</div>
      </div>`;
    }).join('');
  }

  renderInsightsExtra();
}

// ── Load Config ──────────────────────────────────────────────
function loadConfig() {
  fetch('/api/config')
    .then(r => r.json())
    .then(d => {
      buildQuickKeys(d);   // render the "all API keys" quick-add panel with masked placeholders
      const apiEl = document.getElementById('cfgApiKey');
      if (apiEl && d.NVIDIA_API_KEY_MASKED) {
        apiEl.placeholder = 'Current: ' + d.NVIDIA_API_KEY_MASKED;
      }

      const apiDot = document.getElementById('cfgApiDot');
      const apiLbl = document.getElementById('cfgApiLabel');
      if (apiDot) apiDot.className = 'cfg-status-dot ' + (d.NVIDIA_API_KEY_SET ? 'on' : '');
      if (apiLbl) apiLbl.textContent = d.NVIDIA_API_KEY_SET ? 'API Key Set' : 'No API Key';

      if (d.HOTKEY_COMBOS && d.HOTKEY_COMBOS.length) {
        _hotkeys = d.HOTKEY_COMBOS.map(v => ({ value: v, mode: 'preset' }));
        renderHotkeyBuilder();
        renderActiveHotkeys();
        const hkLbl = document.getElementById('cfgHkLabel');
        if (hkLbl) hkLbl.textContent = _hotkeys.length + ' Hotkey' + (_hotkeys.length !== 1 ? 's' : '');
      }

      setToggle('CLEAN_WITH_LLM',     d.CLEAN_WITH_LLM);
      setToggle('APPEND_NEWLINE',     d.APPEND_NEWLINE);
      setToggle('PRESERVE_CLIPBOARD', d.PRESERVE_CLIPBOARD);
      setToggle('EDIT_BEFORE_PASTE',  d.EDIT_BEFORE_PASTE);
      setToggle('AUDIO_CUES',         d.AUDIO_CUES);

      const llmDot = document.getElementById('cfgLlmDot');
      const llmLbl = document.getElementById('cfgLlmLabel');
      if (llmDot) llmDot.className = 'cfg-status-dot ' + (_cfgToggles.CLEAN_WITH_LLM ? 'on' : '');
      if (llmLbl) llmLbl.textContent = _cfgToggles.CLEAN_WITH_LLM ? 'On' : 'Off';

      if (d.ASR_MODEL) setModelValue('cfgAsrModel', 'cfgAsrModelCustom', d.ASR_MODEL);
      if (d.LLM_MODEL) setModelValue('cfgLlmModel', 'cfgLlmModelCustom', d.LLM_MODEL);
      // LANGUAGE: "" = auto-detect, so use nullish check not truthy
      const langEl = document.getElementById('cfgLanguage');
      if (langEl && d.LANGUAGE !== undefined) langEl.value = d.LANGUAGE;
      const translateEl = document.getElementById('cfgTranslateEn');
      if (translateEl && d.TRANSLATE_TO_ENGLISH !== undefined)
        translateEl.checked = !!d.TRANSLATE_TO_ENGLISH;
      if (d.WHISPER_PROMPT != null) {
        const e = document.getElementById('cfgWhisperPrompt');
        if (e) e.value = d.WHISPER_PROMPT;
      }
      if (d.WRITING_STYLE) setWritingStyle(d.WRITING_STYLE, true);
      // Snippets
      if (d.SNIPPETS) { _snippets = Object.assign({}, d.SNIPPETS); renderSnippets(); }
      // VAD Pause Apps
      const vadPauseEl = document.getElementById('cfgVadPauseApps');
      if (vadPauseEl && Array.isArray(d.VAD_PAUSE_APPS))
        vadPauseEl.value = d.VAD_PAUSE_APPS.join('\n');
      if (d.SAMPLE_RATE)      setSelectValue('cfgSampleRate', String(d.SAMPLE_RATE));
      if (d.INPUT_DEVICE != null) setSelectValue('cfgInputDevice', String(d.INPUT_DEVICE || ''));

      if (d.VOICE_VAD_SILENCE_SEC !== undefined) {
        const sl = document.getElementById('cfgVadSilence');
        const sv = document.getElementById('cfgVadSilenceVal');
        if (sl) sl.value = d.VOICE_VAD_SILENCE_SEC;
        if (sv) sv.textContent = parseFloat(d.VOICE_VAD_SILENCE_SEC).toFixed(1) + 's';
      }
      if (d.VOICE_VAD_THRESHOLD !== undefined) {
        const tl = document.getElementById('cfgVadThreshold');
        const tv = document.getElementById('cfgVadThresholdVal');
        if (tl) tl.value = d.VOICE_VAD_THRESHOLD;
        if (tv) tv.textContent = parseFloat(d.VOICE_VAD_THRESHOLD).toFixed(3);
      }

      if (d.HTTP_TIMEOUT != null) { const e = document.getElementById('cfgHttpTimeout'); if (e) e.value = d.HTTP_TIMEOUT; }
      if (d.HTTP_RETRIES != null) { const e = document.getElementById('cfgHttpRetries'); if (e) e.value = d.HTTP_RETRIES; }

      // Backends
      if (d.ASR_BACKEND) { _asrBackend = d.ASR_BACKEND; setAsrBackend(d.ASR_BACKEND); }
      if (d.LLM_BACKEND) { _llmBackend = d.LLM_BACKEND; setLlmBackend(d.LLM_BACKEND); }
      if (d.LLM_FALLBACK_BACKEND !== undefined) {
        const fb = document.getElementById('cfgLlmFallback');
        if (fb) fb.value = d.LLM_FALLBACK_BACKEND || '';
      }
      if (d.GROQ_API_KEY_MASKED) {
        const e = document.getElementById('cfgGroqKey');
        if (e) e.placeholder = 'Current: ' + d.GROQ_API_KEY_MASKED;
      }
      if (d.GROQ_ASR_MODEL) setSelectValue('cfgGroqAsrModel', d.GROQ_ASR_MODEL);
      if (d.GROQ_LLM_MODEL) setSelectValue('cfgGroqLlmModel', d.GROQ_LLM_MODEL);
      if (d.BRAIN_MODE !== undefined) setToggle('BRAIN_MODE', d.BRAIN_MODE);
      if (d.ASR_BRAIN_SECONDARY) setSelectValue('cfgBrainSecondary', d.ASR_BRAIN_SECONDARY);
      if (d.OPENAI_API_KEY_MASKED) {
        const e = document.getElementById('cfgOpenaiKey');
        if (e) e.placeholder = 'Current: ' + d.OPENAI_API_KEY_MASKED;
      }
      if (d.OPENAI_ASR_MODEL) { const e = document.getElementById('cfgOpenaiAsrModel'); if (e) setSelectValue('cfgOpenaiAsrModel', d.OPENAI_ASR_MODEL); }
      if (d.OPENAI_LLM_MODEL) setSelectValue('cfgOpenaiLlmModel', d.OPENAI_LLM_MODEL);
      if (d.ANTHROPIC_API_KEY_MASKED) {
        const e = document.getElementById('cfgAnthropicKey');
        if (e) e.placeholder = 'Current: ' + d.ANTHROPIC_API_KEY_MASKED;
      }
      if (d.ANTHROPIC_LLM_MODEL) setSelectValue('cfgAnthropicModel', d.ANTHROPIC_LLM_MODEL);
      if (d.CEREBRAS_API_KEY_MASKED) {
        const e = document.getElementById('cfgCerebrasKey');
        if (e) e.placeholder = 'Current: ' + d.CEREBRAS_API_KEY_MASKED;
      }
      if (d.CEREBRAS_LLM_MODEL) setSelectValue('cfgCerebrasLlmModel', d.CEREBRAS_LLM_MODEL);
      if (d.GEMINI_API_KEY_MASKED) {
        const e = document.getElementById('cfgGeminiKey');
        if (e) e.placeholder = 'Current: ' + d.GEMINI_API_KEY_MASKED;
      }
      if (d.GEMINI_LLM_MODEL) setSelectValue('cfgGeminiLlmModel', d.GEMINI_LLM_MODEL);
      if (d.OLLAMA_BASE_URL) { const e = document.getElementById('cfgOllamaUrl'); if (e) e.value = d.OLLAMA_BASE_URL; }
      if (d.OLLAMA_LLM_MODEL) { const e = document.getElementById('cfgOllamaModel'); if (e) e.value = d.OLLAMA_LLM_MODEL; }
      if (d.LOCAL_ASR_URL) { const e = document.getElementById('cfgLocalAsrUrl'); if (e) e.value = d.LOCAL_ASR_URL; }
      if (d.LOCAL_ASR_MODEL) { const e = document.getElementById('cfgLocalAsrModel'); if (e) e.value = d.LOCAL_ASR_MODEL; }
      if (d.CUSTOM_ASR_URL) { const e = document.getElementById('cfgCustomAsrUrl'); if (e) e.value = d.CUSTOM_ASR_URL; }
      if (d.CUSTOM_ASR_MODEL) { const e = document.getElementById('cfgCustomAsrModel'); if (e) e.value = d.CUSTOM_ASR_MODEL; }
      if (d.CUSTOM_LLM_URL) { const e = document.getElementById('cfgCustomLlmUrl'); if (e) e.value = d.CUSTOM_LLM_URL; }
      if (d.CUSTOM_LLM_MODEL) { const e = document.getElementById('cfgCustomLlmModel'); if (e) e.value = d.CUSTOM_LLM_MODEL; }
      if (d.CUSTOM_API_KEY_MASKED) {
        const e = document.getElementById('cfgCustomKey');
        if (e) e.placeholder = 'Current: ' + d.CUSTOM_API_KEY_MASKED;
      }
      if (d.DEEPGRAM_API_KEY_MASKED) {
        const e = document.getElementById('cfgDeepgramKey');
        if (e) e.placeholder = 'Current: ' + d.DEEPGRAM_API_KEY_MASKED;
      }
      if (d.DEEPGRAM_ASR_MODEL) setSelectValue('cfgDeepgramAsrModel', d.DEEPGRAM_ASR_MODEL);
      if (d.DEEPGRAM_DIARIZE !== undefined) setToggle('DEEPGRAM_DIARIZE', d.DEEPGRAM_DIARIZE);

      // Wake word
      setToggle('WAKE_WORD_ENABLED', d.WAKE_WORD_ENABLED);
      if (d.VOICE_SIMILARITY_THRESHOLD != null) {
        const vt = document.getElementById('cfgVoiceSimThreshold');
        const vv = document.getElementById('voiceSimVal');
        if (vt) vt.value = d.VOICE_SIMILARITY_THRESHOLD;
        if (vv) vv.textContent = parseFloat(d.VOICE_SIMILARITY_THRESHOLD).toFixed(2);
      }
      if (d.VOICE_ENROLLMENT_ENABLED !== undefined) setToggle('VOICE_ENROLLMENT_ENABLED', d.VOICE_ENROLLMENT_ENABLED);
      const wakeVal = d.WAKE_WORD || '';
      const legacyWW = document.getElementById('cfgWakeWord');
      if (legacyWW) legacyWW.value = wakeVal;
      const inlineWW = document.getElementById('cfgWakeWordInline');
      if (inlineWW) inlineWW.value = wakeVal;
      if (d.WAKE_WORD_VAD_THRESHOLD !== undefined) {
        const wt = document.getElementById('cfgVadThreshold');
        const wv = document.getElementById('cfgVadThresholdVal');
        if (wt) wt.value = d.WAKE_WORD_VAD_THRESHOLD;
        if (wv) wv.textContent = parseFloat(d.WAKE_WORD_VAD_THRESHOLD).toFixed(3);
      }
      if (d.WAKE_WORD_SILENCE_SEC !== undefined) {
        const el = document.getElementById('cfgWakeSilenceSec');
        const lbl = document.getElementById('cfgWakeSilenceSecVal');
        if (el) el.value = d.WAKE_WORD_SILENCE_SEC;
        if (lbl) lbl.textContent = parseFloat(d.WAKE_WORD_SILENCE_SEC).toFixed(2) + 's';
      }
      if (d.WAKE_WORD_TRIGGER_SEC !== undefined) {
        const el = document.getElementById('cfgWakeTriggerSec');
        const lbl = document.getElementById('cfgWakeTriggerSecVal');
        if (el) el.value = d.WAKE_WORD_TRIGGER_SEC;
        if (lbl) lbl.textContent = parseFloat(d.WAKE_WORD_TRIGGER_SEC).toFixed(2) + 's';
      }
      if (d.WAKE_WORD_MAX_RECORD_SEC !== undefined) {
        const el = document.getElementById('cfgWakeMaxRecordSec');
        const lbl = document.getElementById('cfgWakeMaxRecordSecVal');
        if (el) el.value = d.WAKE_WORD_MAX_RECORD_SEC;
        if (lbl) lbl.textContent = parseFloat(d.WAKE_WORD_MAX_RECORD_SEC).toFixed(1) + 's';
      }
      if (d.WAKE_WORD_LISTEN_SEC !== undefined) {
        const el = document.getElementById('cfgWakeListenSec');
        const lbl = document.getElementById('cfgWakeListenSecVal');
        if (el) el.value = d.WAKE_WORD_LISTEN_SEC;
        if (lbl) lbl.textContent = parseFloat(d.WAKE_WORD_LISTEN_SEC).toFixed(0) + 's';
      }

      // Startup & Display
      setToggle('START_MINIMIZED',      d.START_MINIMIZED);
      setToggle('SHOW_WIDGET_ON_START', d.SHOW_WIDGET_ON_START !== false);

      // Audio cue detail
      setToggle('AUDIO_CUE_START', d.AUDIO_CUE_START !== false);
      setToggle('AUDIO_CUE_STOP',  d.AUDIO_CUE_STOP  !== false);
      setToggle('AUDIO_CUE_ERROR', d.AUDIO_CUE_ERROR !== false);

      // Privacy & Data
      setToggle('PRIVACY_MODE',      d.PRIVACY_MODE);
      setToggle('CONTEXT_AWARENESS', d.CONTEXT_AWARENESS !== false);
      const adEl = document.getElementById('cfgAutoDeleteDays');
      if (adEl) adEl.value = d.HISTORY_AUTO_DELETE_DAYS || 0;

      // Autostart (also refreshed separately via loadAutostart)
      loadAutostart();

      // Load per-hotkey configs into the builder
      if (Array.isArray(d.HOTKEY_CONFIGS) && d.HOTKEY_CONFIGS.length) {
        _hotkeys = d.HOTKEY_CONFIGS.map(hc => ({
          value: hc.combo || '',
          mode: 'preset',
          recordMode: hc.mode || 'hold',
        }));
        renderHotkeyBuilder();
        renderActiveHotkeys();
      } else if (Array.isArray(d.HOTKEY_COMBOS) && d.HOTKEY_COMBOS.length) {
        const rm = d.RECORDING_MODE === 'toggle' ? 'toggle' : 'hold';
        _hotkeys = d.HOTKEY_COMBOS.map(c => ({ value: c, mode: 'preset', recordMode: rm }));
        renderHotkeyBuilder();
        renderActiveHotkeys();
      }

      // Sync voice card with WAKE_WORD_ENABLED (must come after setToggle calls above)
      setRecordingMode();

      _hasChanges = false;
      const btn = document.getElementById('saveCfgBtn');
      if (btn) { btn.textContent = '[ SAVE CHANGES ]'; btn.style.boxShadow = ''; }
      const ub = document.getElementById('unsavedBanner');
      if (ub) ub.style.display = 'none';
    })
    .catch(() => {});
}

// ── Model select helpers ─────────────────────────────────────
function setModelValue(selectId, customInputId, val) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  let found = false;
  for (const opt of sel.options) {
    if (opt.value === val && opt.value !== '__custom__') {
      opt.selected = true;
      found = true;
      break;
    }
  }
  if (!found && val) {
    for (const opt of sel.options) {
      if (opt.value === '__custom__') { opt.selected = true; break; }
    }
    const ci = document.getElementById(customInputId);
    if (ci) { ci.value = val; ci.style.display = ''; }
  }
}

function getModelValue(selectId, customInputId) {
  const sel = document.getElementById(selectId);
  if (!sel) return '';
  if (sel.value === '__custom__') {
    const ci = document.getElementById(customInputId);
    return (ci?.value?.trim()) || '';
  }
  return sel.value;
}

function handleModelChange(type, value) {
  const customId = type === 'asr' ? 'cfgAsrModelCustom' : 'cfgLlmModelCustom';
  const ci = document.getElementById(customId);
  if (ci) {
    ci.style.display = value === '__custom__' ? '' : 'none';
    if (value !== '__custom__') ci.value = '';
  }
  markUnsaved();
}

// ── Active Hotkeys Display ───────────────────────────────────
function renderActiveHotkeys() {
  const el = document.getElementById('activeHotkeyKeys');
  if (!el) return;
  const first = _hotkeys[0];
  if (!first || !first.value) {
    el.innerHTML = '<span class="key">—</span>';
    return;
  }
  el.innerHTML = formatHotkeyKeys(first.value)
    .map(k => `<span class="key">${escHtml(k)}</span>`).join('');
}

function formatHotkeyKeys(val) {
  if (!val) return [];
  return (val.match(/<[^>]+>|[^+]+/g) || [])
    .filter(p => p && p !== '+')
    .map(p => p.replace(/[<>]/g, '').toUpperCase());
}

// ── Hotkey Builder ───────────────────────────────────────────
function renderHotkeyBuilder() {
  const el = document.getElementById('hkBuilder');
  if (!el) return;

  let html = _hotkeys.map((combo, idx) => {
    const previewKeys = combo.value
      ? formatHotkeyKeys(combo.value).map(k => `<span class="key">${escHtml(k)}</span>`).join('')
      : '';
    const inputHtml = combo.mode === 'preset'
      ? `<select class="cfg-select" onchange="updateHotkeyValue(${idx}, this.value)">
           <option value="">— Select a hotkey —</option>
           ${HOTKEY_PRESETS.map(p =>
             `<option value="${escHtml(p.value)}" ${combo.value === p.value ? 'selected' : ''}>${escHtml(p.label)}</option>`
           ).join('')}
         </select>`
      : `<input class="cfg-in" value="${escHtml(combo.value)}"
           placeholder="e.g. &lt;ctrl&gt;+&lt;shift&gt;+&lt;space&gt;"
           oninput="updateHotkeyValue(${idx}, this.value)" spellcheck="false">`;
    const recMode  = combo.recordMode || 'hold';
    const rmLabel  = recMode === 'hold' ? '◼ HOLD' : '◈ TOGGLE';
    const rmColor  = recMode === 'hold' ? 'var(--vf-cyan)' : 'var(--vf-pri)';
    const rmPill   = `<button style="font-size:9px;letter-spacing:1px;padding:2px 7px;border-radius:3px;` +
      `border:1px solid ${rmColor};color:${rmColor};background:transparent;cursor:pointer;` +
      `white-space:nowrap;font-family:inherit" onclick="toggleHotkeyRecordMode(${idx})" ` +
      `title="HOLD: hold key while speaking, release to stop | TOGGLE: press once to start, press again to stop">` +
      `${rmLabel}</button>`;
    return `<div class="hk-entry">
      <div class="hk-num">${idx + 1}</div>
      <div class="hk-content">
        ${inputHtml}
        ${previewKeys ? `<div class="hk-preview">${previewKeys}</div>` : ''}
      </div>
      <div class="hk-actions">
        ${rmPill}
        <button class="hk-mode-btn" onclick="toggleHotkeyMode(${idx})"
          title="${combo.mode === 'preset' ? 'Switch to custom input' : 'Switch to preset'}"
        >${combo.mode === 'preset' ? '✎' : '☰'}</button>
        <button class="hk-remove-btn" onclick="removeHotkey(${idx})" title="Remove">✕</button>
      </div>
    </div>`;
  }).join('');

  html += `<button class="hk-add-btn" onclick="addHotkey()">
    <span class="hk-add-icon">+</span>
    ADD HOTKEY COMBO
    <span style="margin-left:auto;opacity:0.5">${_hotkeys.length}/5</span>
  </button>
  <div style="font-size:10px;color:var(--vf-muted);margin-top:6px;line-height:1.6">
    Multiple hotkeys let you trigger dictation from different key combos.
  </div>`;

  el.innerHTML = html;
  _renderModeHotkeys();
}

function addHotkey() {
  if (_hotkeys.length >= 5) { showToast('MAX 5 HOTKEYS'); return; }
  _hotkeys.push({ value: '', mode: 'preset', recordMode: 'hold' });
  renderHotkeyBuilder();
  markUnsaved();
}

function removeHotkey(idx) {
  if (_hotkeys.length <= 1) { showToast('NEED AT LEAST 1 HOTKEY'); return; }
  _hotkeys.splice(idx, 1);
  renderHotkeyBuilder();
  markUnsaved();
}

function updateHotkeyValue(idx, val) {
  if (_hotkeys[idx]) _hotkeys[idx].value = val;
  renderHotkeyBuilder();
  markUnsaved();
}

function toggleHotkeyMode(idx) {
  if (_hotkeys[idx]) {
    _hotkeys[idx].mode = _hotkeys[idx].mode === 'preset' ? 'custom' : 'preset';
    renderHotkeyBuilder();
  }
}

function toggleHotkeyRecordMode(idx) {
  if (_hotkeys[idx]) {
    _hotkeys[idx].recordMode = (_hotkeys[idx].recordMode || 'hold') === 'hold' ? 'toggle' : 'hold';
    renderHotkeyBuilder();
    markUnsaved();
  }
}

function resetHotkeys() {
  _hotkeys = [
    { value: '<ctrl>+<alt>+v',       mode: 'preset', recordMode: 'hold' },
    { value: '<ctrl>+<alt>+<space>', mode: 'preset', recordMode: 'toggle' },
    { value: '<scroll_lock>',        mode: 'preset', recordMode: 'hold' },
  ];
  renderHotkeyBuilder();
  renderActiveHotkeys();
  markUnsaved();
  showToast('HOTKEYS RESET TO DEFAULTS');
}

// ── Toggle Helpers ───────────────────────────────────────────
function setToggle(cfgKey, value) {
  _cfgToggles[cfgKey] = !!value;
  // Update all toggle elements mapped to this config key
  Object.entries(TOGGLE_MAP).forEach(([domId, key]) => {
    if (key !== cfgKey) return;
    const wrap = document.getElementById(domId);
    const tog  = wrap?.querySelector('.tog');
    if (tog) tog.classList.toggle('tog-on', !!value);
  });
  // Sync dedicated voice-activation toggle
  if (cfgKey === 'WAKE_WORD_ENABLED') {
    const tog2 = document.getElementById('togWakeWordMode')?.querySelector('.tog');
    if (tog2) tog2.classList.toggle('tog-on', !!value);
  }
}

function toggleVoiceActivation() {
  toggleCfg('WAKE_WORD_ENABLED');
  const tog2 = document.getElementById('togWakeWordMode')?.querySelector('.tog');
  if (tog2) tog2.classList.toggle('tog-on', _cfgToggles.WAKE_WORD_ENABLED);
  setRecordingMode(); // sync voice card active state
}

function toggleCfg(cfgKey) {
  _cfgToggles[cfgKey] = !_cfgToggles[cfgKey];
  const domId = Object.keys(TOGGLE_MAP).find(k => TOGGLE_MAP[k] === cfgKey);
  if (domId) {
    const wrap = document.getElementById(domId);
    const tog  = wrap?.querySelector('.tog');
    if (tog) tog.classList.toggle('tog-on', _cfgToggles[cfgKey]);
  }
  if (cfgKey === 'CLEAN_WITH_LLM') {
    const dot = document.getElementById('cfgLlmDot');
    const lbl = document.getElementById('cfgLlmLabel');
    if (dot) dot.className = 'cfg-status-dot ' + (_cfgToggles.CLEAN_WITH_LLM ? 'on' : '');
    if (lbl) lbl.textContent = _cfgToggles.CLEAN_WITH_LLM ? 'On' : 'Off';
  }
  markUnsaved();
}

// ── Save Config ──────────────────────────────────────────────
function markUnsaved() {
  _hasChanges = true;
  const btn = document.getElementById('saveCfgBtn');
  if (btn) { btn.textContent = '[ SAVE CHANGES ]'; btn.style.boxShadow = '0 0 12px var(--vf-pri-glow)'; }
  const ub = document.getElementById('unsavedBanner');
  if (ub) ub.style.display = 'flex';
}

function saveConfig() {
  const emptyHk = _hotkeys.some(h => !h.value);
  if (emptyHk) { showToast('FILL ALL HOTKEY FIELDS'); return; }

  const asrModel = getModelValue('cfgAsrModel', 'cfgAsrModelCustom');
  const llmModel = getModelValue('cfgLlmModel', 'cfgLlmModelCustom');

  const cfg = {
    HOTKEY_CONFIGS:        _hotkeys.map(h => ({ combo: h.value, mode: h.recordMode || 'hold' })),
    RECORDING_MODE:        _cfgToggles.WAKE_WORD_ENABLED ? 'voice' : 'hold',
    CLEAN_WITH_LLM:        _cfgToggles.CLEAN_WITH_LLM,
    APPEND_NEWLINE:        _cfgToggles.APPEND_NEWLINE,
    PRESERVE_CLIPBOARD:    _cfgToggles.PRESERVE_CLIPBOARD,
    EDIT_BEFORE_PASTE:     _cfgToggles.EDIT_BEFORE_PASTE,
    AUDIO_CUES:            _cfgToggles.AUDIO_CUES,
    VOICE_VAD_SILENCE_SEC: parseFloat(document.getElementById('cfgVadSilence')?.value || '2'),
    VOICE_VAD_THRESHOLD:   parseFloat(document.getElementById('cfgVadThreshold')?.value || '0.030'),
    ASR_MODEL:             asrModel,
    LLM_MODEL:             llmModel,
    LANGUAGE:              document.getElementById('cfgLanguage')?.value ?? '',
    TRANSLATE_TO_ENGLISH:  document.getElementById('cfgTranslateEn')?.checked ?? false,
    WHISPER_PROMPT:        document.getElementById('cfgWhisperPrompt')?.value ?? '',
    WRITING_STYLE:         _writingStyle,
    SNIPPETS:              _snippets,
    VAD_PAUSE_APPS: (document.getElementById('cfgVadPauseApps')?.value || '')
                      .split('\n').map(s => s.trim()).filter(Boolean),
    HTTP_TIMEOUT:          parseInt(document.getElementById('cfgHttpTimeout')?.value || '30'),
    HTTP_RETRIES:          parseInt(document.getElementById('cfgHttpRetries')?.value || '3'),
    SAMPLE_RATE:           parseInt(document.getElementById('cfgSampleRate')?.value || '16000'),
    INPUT_DEVICE:          document.getElementById('cfgInputDevice')?.value || '',
  };

  const apiKey = document.getElementById('cfgApiKey')?.value.trim();
  if (apiKey) cfg.NVIDIA_API_KEY = apiKey;

  // Backend fields
  cfg.ASR_BACKEND = _asrBackend;
  cfg.LLM_BACKEND = _llmBackend;
  cfg.LLM_FALLBACK_BACKEND = document.getElementById('cfgLlmFallback')?.value || '';

  const groqKey = document.getElementById('cfgGroqKey')?.value.trim();
  if (groqKey) cfg.GROQ_API_KEY = groqKey;
  cfg.GROQ_ASR_MODEL = document.getElementById('cfgGroqAsrModel')?.value || 'whisper-large-v3-turbo';
  cfg.GROQ_LLM_MODEL = document.getElementById('cfgGroqLlmModel')?.value || 'llama-3.3-70b-versatile';

  cfg.BRAIN_MODE = _cfgToggles.BRAIN_MODE;
  cfg.ASR_BRAIN_SECONDARY = document.getElementById('cfgBrainSecondary')?.value || 'openai';

  const openaiKey = document.getElementById('cfgOpenaiKey')?.value.trim();
  if (openaiKey) cfg.OPENAI_API_KEY = openaiKey;
  const anthropicKey = document.getElementById('cfgAnthropicKey')?.value.trim();
  if (anthropicKey) cfg.ANTHROPIC_API_KEY = anthropicKey;
  const cerebrasKey = document.getElementById('cfgCerebrasKey')?.value.trim();
  if (cerebrasKey) cfg.CEREBRAS_API_KEY = cerebrasKey;
  const geminiKey = document.getElementById('cfgGeminiKey')?.value.trim();
  if (geminiKey) cfg.GEMINI_API_KEY = geminiKey;
  const customKey = document.getElementById('cfgCustomKey')?.value.trim();
  if (customKey) cfg.CUSTOM_API_KEY = customKey;
  const deepgramKey = document.getElementById('cfgDeepgramKey')?.value.trim();
  if (deepgramKey) cfg.DEEPGRAM_API_KEY = deepgramKey;

  cfg.OPENAI_ASR_MODEL = document.getElementById('cfgOpenaiAsrModel')?.value || 'whisper-1';
  cfg.OPENAI_LLM_MODEL = document.getElementById('cfgOpenaiLlmModel')?.value || 'gpt-4o-mini';
  cfg.ANTHROPIC_LLM_MODEL = document.getElementById('cfgAnthropicModel')?.value || 'claude-3-5-haiku-20241022';
  cfg.CEREBRAS_LLM_MODEL = document.getElementById('cfgCerebrasLlmModel')?.value || 'llama-3.3-70b';
  cfg.GEMINI_LLM_MODEL = document.getElementById('cfgGeminiLlmModel')?.value || 'gemini-2.5-flash';
  cfg.OLLAMA_BASE_URL  = document.getElementById('cfgOllamaUrl')?.value?.trim() || '';
  cfg.OLLAMA_LLM_MODEL = document.getElementById('cfgOllamaModel')?.value?.trim() || '';
  cfg.LOCAL_ASR_URL   = document.getElementById('cfgLocalAsrUrl')?.value?.trim() || '';
  cfg.LOCAL_ASR_MODEL = document.getElementById('cfgLocalAsrModel')?.value?.trim() || '';
  cfg.CUSTOM_ASR_URL   = document.getElementById('cfgCustomAsrUrl')?.value?.trim() || '';
  cfg.CUSTOM_ASR_MODEL = document.getElementById('cfgCustomAsrModel')?.value?.trim() || '';
  cfg.CUSTOM_LLM_URL   = document.getElementById('cfgCustomLlmUrl')?.value?.trim() || '';
  cfg.CUSTOM_LLM_MODEL = document.getElementById('cfgCustomLlmModel')?.value?.trim() || '';
  cfg.DEEPGRAM_ASR_MODEL = document.getElementById('cfgDeepgramAsrModel')?.value || 'nova-3';
  cfg.DEEPGRAM_DIARIZE   = _cfgToggles.DEEPGRAM_DIARIZE || false;

  // Wake word
  cfg.WAKE_WORD_ENABLED = _cfgToggles.WAKE_WORD_ENABLED;
  const _vsim = document.getElementById('cfgVoiceSimThreshold');
  if (_vsim) cfg.VOICE_SIMILARITY_THRESHOLD = parseFloat(_vsim.value);
  if (_cfgToggles.VOICE_ENROLLMENT_ENABLED !== undefined) cfg.VOICE_ENROLLMENT_ENABLED = _cfgToggles.VOICE_ENROLLMENT_ENABLED;
  cfg.WAKE_WORD = (document.getElementById('cfgWakeWordInline')?.value?.trim()
               || document.getElementById('cfgWakeWord')?.value?.trim()
               || '');
  const _wwThr = document.getElementById('cfgVadThreshold');
  if (_wwThr) cfg.WAKE_WORD_VAD_THRESHOLD = parseFloat(_wwThr.value);
  cfg.WAKE_WORD_SILENCE_SEC    = parseFloat(document.getElementById('cfgWakeSilenceSec')?.value || '0.55');
  cfg.WAKE_WORD_TRIGGER_SEC    = parseFloat(document.getElementById('cfgWakeTriggerSec')?.value || '0.15');
  cfg.WAKE_WORD_MAX_RECORD_SEC = parseFloat(document.getElementById('cfgWakeMaxRecordSec')?.value || '2.5');
  cfg.WAKE_WORD_LISTEN_SEC     = parseFloat(document.getElementById('cfgWakeListenSec')?.value || '12');

  // Startup & Display
  cfg.START_MINIMIZED      = _cfgToggles.START_MINIMIZED;
  cfg.SHOW_WIDGET_ON_START = _cfgToggles.SHOW_WIDGET_ON_START;

  // Audio cue detail
  cfg.AUDIO_CUE_START = _cfgToggles.AUDIO_CUE_START;
  cfg.AUDIO_CUE_STOP  = _cfgToggles.AUDIO_CUE_STOP;
  cfg.AUDIO_CUE_ERROR = _cfgToggles.AUDIO_CUE_ERROR;

  // Privacy & Data
  cfg.PRIVACY_MODE      = _cfgToggles.PRIVACY_MODE;
  cfg.CONTEXT_AWARENESS = _cfgToggles.CONTEXT_AWARENESS;
  const _adDays = document.getElementById('cfgAutoDeleteDays');
  if (_adDays) cfg.HISTORY_AUTO_DELETE_DAYS = parseInt(_adDays.value) || 0;

  Object.keys(cfg).forEach(k => { if (cfg[k] == null) delete cfg[k]; });

  fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        _hasChanges = false;
        const btn = document.getElementById('saveCfgBtn');
        if (btn) { btn.textContent = '[ SAVED ]'; btn.style.boxShadow = ''; }
        const ub = document.getElementById('unsavedBanner');
        if (ub) ub.style.display = 'none';
        const ok = document.getElementById('saveOkMsg');
        if (ok) { ok.classList.add('show'); setTimeout(() => ok.classList.remove('show'), 3000); }
        showToast('CONFIG SAVED');
        renderActiveHotkeys();
        const apiEl = document.getElementById('cfgApiKey');
        if (apiEl && apiEl.value) apiEl.value = '';
      } else {
        showToast('SAVE FAILED: ' + (d.error || 'unknown error'));
      }
    })
    .catch(() => showToast('SAVE FAILED'));
}

// ── Auto-save API keys on paste/blur ─────────────────────────
// Pasting a key and immediately clicking "Test" used to require knowing to
// click "[ SAVE CHANGES ]" first — confusing, and easy to miss. Saving the
// key the moment the field loses focus means the key is in the backend by
// the time the user reaches for Test, and they never have to think about it.
function autoSaveApiKey(configKey, inputEl) {
  const val = (inputEl.value || '').trim();
  if (!val) return;
  fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [configKey]: val }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) showToast(configKey.replace('_API_KEY', '') + ' KEY SAVED ✓');
    })
    .catch(() => {});
}

// ── Test API Key ─────────────────────────────────────────────
// Maps the active ASR backend to the input field holding its key, so "Test"
// checks what's currently typed — not the last-saved value. Without this,
// typing a key and clicking Test (before Save) reports "not set", which
// looks like the key was never accepted.
const _ASR_KEY_FIELD = {
  groq: 'cfgGroqKey',
  deepgram: 'cfgDeepgramKey',
  openai: 'cfgOpenaiKey',
  nvidia: 'cfgApiKey',
  custom: 'cfgCustomKey',
};

// Backend → [inputFieldId, configKey] for both ASR and LLM backends.
const _BACKEND_KEY_MAP = {
  groq:     ['cfgGroqKey',     'GROQ_API_KEY'],
  deepgram: ['cfgDeepgramKey', 'DEEPGRAM_API_KEY'],
  openai:   ['cfgOpenaiKey',   'OPENAI_API_KEY'],
  nvidia:   ['cfgApiKey',      'NVIDIA_API_KEY'],
  anthropic:['cfgAnthropicKey','ANTHROPIC_API_KEY'],
  cerebras: ['cfgCerebrasKey', 'CEREBRAS_API_KEY'],
  gemini:   ['cfgGeminiKey',   'GEMINI_API_KEY'],
  custom:   ['cfgCustomKey',   'CUSTOM_API_KEY'],
};

// Save the currently-typed key for a backend. Always resolves so callers
// can chain with .then() regardless of whether a key was present.
function _saveKeyFor(backend) {
  const pair = _BACKEND_KEY_MAP[backend];
  if (!pair) return Promise.resolve();
  const [fieldId, configKey] = pair;
  const val = (document.getElementById(fieldId)?.value || '').trim();
  if (!val) return Promise.resolve();
  return fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [configKey]: val }),
  }).catch(() => {});
}

// Convenience wrappers used by the test buttons.
function _saveTypedKeyNow()    { return _saveKeyFor(_asrBackend); }

function testApiKey(backend) {
  // Test the backend whose key field this button belongs to — falling back to
  // the active ASR pill. Passing it explicitly means "add a Deepgram key" tests
  // Deepgram even when Groq is still the saved primary (was the source of the
  // misleading "Invalid <wrong-provider> key" errors).
  backend = backend || _asrBackend;
  const btn = document.getElementById('testApiBtn');
  const status = document.getElementById('cfgApiStatus');
  if (btn) btn.textContent = 'SAVING...';
  if (status) status.textContent = '— Saving key…';

  const fieldId = _ASR_KEY_FIELD[backend];
  const typedKey = fieldId ? (document.getElementById(fieldId)?.value || '').trim() : '';

  // Always save the typed key first — covers the case where the user pastes
  // and immediately clicks TEST without the field ever losing focus (which
  // would have triggered onchange). Then test the now-saved value.
  _saveKeyFor(backend).then(() => {
    if (btn) btn.textContent = 'TESTING...';
    if (status) status.textContent = '— Testing...';
    return fetch('/api/test-api-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: typedKey, backend: backend }),
    });
  })
    .then(r => r.json())
    .then(d => {
      if (btn) btn.textContent = 'TEST';
      if (d.ok) {
        if (status) { status.textContent = '✓ ' + (d.message || 'API key valid'); status.style.color = 'var(--vf-cyan)'; }
        const dot = document.getElementById('cfgApiDot');
        const lbl = document.getElementById('cfgApiLabel');
        if (dot) dot.className = 'cfg-status-dot on';
        if (lbl) lbl.textContent = 'API Connected';
        showToast('API KEY VALID ✓');
      } else {
        if (status) { status.textContent = '✕ ' + (d.error || 'Invalid key'); status.style.color = 'var(--vf-red)'; }
        showToast('API ERROR: ' + (d.error || 'invalid key').substring(0, 40));
      }
    })
    .catch(() => {
      if (btn) btn.textContent = 'TEST';
      if (status) { status.textContent = '✕ Connection failed'; status.style.color = 'var(--vf-red)'; }
      showToast('API TEST FAILED');
    });
}

// ── Test LLM Backend ─────────────────────────────────────────
function testLlm(backend) {
  // Test the LLM backend the user is configuring (active pill by default), so
  // adding a Cerebras/Gemini/Anthropic key tests that provider — not whatever
  // LLM backend happens to be saved as active.
  backend = backend || _llmBackend;
  const btn    = document.getElementById('testLlmBtn');
  const status = document.getElementById('cfgLlmStatus');
  const result = document.getElementById('cfgLlmTestResult');
  if (btn) btn.textContent = 'SAVING...';
  if (status) status.textContent = '— Saving key…';
  if (result) result.style.display = 'none';

  _saveKeyFor(backend).then(() => {
    if (btn) btn.textContent = 'TESTING...';
    if (status) status.textContent = '— Sending test phrase to LLM…';
    return fetch('/api/test-llm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ backend: backend }),
    });
  })
    .then(r => r.json())
    .then(d => {
      if (btn) btn.textContent = 'TEST LLM';
      if (d.ok) {
        if (status) { status.textContent = '✓ ' + d.message; status.style.color = 'var(--vf-cyan)'; }
        if (result) {
          result.innerHTML =
            '<span style="color:var(--vf-muted)">IN: </span>' + escHtml(d.input) + '<br><br>' +
            '<span style="color:var(--vf-cyan)">OUT: </span><strong>' + escHtml(d.output) + '</strong>';
          result.style.display = 'block';
        }
        showToast('LLM WORKING ✓');
      } else {
        if (status) { status.textContent = '✕ ' + (d.error || 'LLM failed'); status.style.color = 'var(--vf-red)'; }
        showToast('LLM ERROR: ' + (d.error || 'check backend').substring(0, 50));
      }
    })
    .catch(() => {
      if (btn) btn.textContent = 'TEST LLM';
      if (status) { status.textContent = '✕ Request failed'; status.style.color = 'var(--vf-red)'; }
    });
}

function escHtml(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Collapsible Sections ─────────────────────────────────────
function toggleSection(hd) {
  const block = hd.closest('.cfg-block');
  if (!block) return;
  const isOpen = hd.dataset.open !== 'false';
  const willOpen = !isOpen;
  hd.dataset.open = willOpen ? 'true' : 'false';

  const arrow = hd.querySelector('.cfg-arrow');
  if (arrow) arrow.style.transform = willOpen ? 'rotate(90deg)' : 'rotate(0deg)';
  const label = hd.querySelector('.cfg-collapse-label');
  if (label) label.textContent = willOpen ? 'COLLAPSE' : 'EXPAND';

  Array.from(block.children).forEach(child => {
    if (child === hd) return;
    child.style.display = willOpen ? '' : 'none';
  });
}

// ── Appearance ───────────────────────────────────────────────
function applyTheme(theme) {
  _currentTheme = theme;
  document.body.setAttribute('data-theme', theme);
  const sb = document.getElementById('sbTheme');
  if (sb) sb.textContent = theme.toUpperCase();

  document.querySelectorAll('.theme-card').forEach(b => {
    b.classList.toggle('active', b.dataset.theme === theme);
  });
  localStorage.setItem('vf-theme', theme);
}

function applyAccent(accent) {
  const c = ACCENT_COLORS[accent];
  if (!c) return;
  _currentAccent = accent;
  const root = document.documentElement;
  root.style.setProperty('--vf-pri',      c.pri);
  root.style.setProperty('--vf-pri-dim',  c.dim);
  root.style.setProperty('--vf-pri-bg',   c.bg);
  root.style.setProperty('--vf-pri-glow', c.glow);

  document.querySelectorAll('.accent-swatch').forEach(s => {
    const isActive = s.dataset.accent === accent;
    s.classList.toggle('active', isActive);
    s.innerHTML = isActive ? '<span class="accent-check">✓</span>' : '';
  });
  const nameEl = document.getElementById('accentColorName');
  if (nameEl) nameEl.textContent = c.name;
  localStorage.setItem('vf-accent', accent);
}

function applyUiFont(fontKey) {
  const f = FONTS_UI[fontKey];
  if (!f) return;
  document.documentElement.style.setProperty('--vf-font-b', f);
  localStorage.setItem('vf-font-b', fontKey);
  showToast('FONT: ' + fontKey.toUpperCase().replace('-', ' '));
}

function applyDisplayFont(fontKey) {
  const f = FONTS_DISPLAY[fontKey];
  if (!f) return;
  document.documentElement.style.setProperty('--vf-font-d', f);
  localStorage.setItem('vf-font-d', fontKey);
  showToast('DISPLAY FONT UPDATED');
}

function toggleEffects() {
  const wrap = document.getElementById('togEffects');
  const tog  = wrap?.querySelector('.tog');
  if (!tog) return;
  const on = tog.classList.toggle('tog-on');
  document.body.setAttribute('data-effects', String(on));
}

function toggleLightMode() {
  _lightMode = !_lightMode;
  const wrap = document.getElementById('togLightMode');
  const tog  = wrap?.querySelector('.tog');
  if (tog) tog.classList.toggle('tog-on', _lightMode);
  document.body.setAttribute('data-light', String(_lightMode));
  showToast(_lightMode ? 'LIGHT MODE ON' : 'DARK MODE ON');
}

function restoreAppearance() {
  const theme  = localStorage.getItem('vf-theme');
  const accent = localStorage.getItem('vf-accent');
  const fontB  = localStorage.getItem('vf-font-b');
  const fontD  = localStorage.getItem('vf-font-d');
  const widget = localStorage.getItem('vf-widget');

  if (theme)  applyTheme(theme);
  if (accent) applyAccent(accent);
  if (fontB) {
    applyUiFont(fontB);
    const sel = document.getElementById('cfgUiFont');
    if (sel) sel.value = fontB;
  }
  if (fontD) {
    applyDisplayFont(fontD);
    const sel = document.getElementById('cfgDisplayFont');
    if (sel) sel.value = fontD;
  }
  if (widget) setWidgetStyle(widget);
}

// ── Writing Style ────────────────────────────────────────────
let _writingStyle = 'flow';
const _writingStyleDescs = {
  flow:         'How the LLM rewrites your speech. FLOW: clean prose, light restructuring, preserves your vocabulary (default).',
  verbatim:     'How the LLM rewrites your speech. VERBATIM: minimal changes — only removes filler words and fixes punctuation, never restructures.',
  professional: 'How the LLM rewrites your speech. PROFESSIONAL: formal, polished prose; condenses redundant phrasing.',
  concise:      'How the LLM rewrites your speech. CONCISE: strips everything to the essential point, ~30-50% shorter.',
};
function setWritingStyle(style, skipMark) {
  _writingStyle = style;
  ['Flow', 'Verbatim', 'Professional', 'Concise'].forEach(s => {
    const btn = document.getElementById('btnWritingStyle' + s);
    if (!btn) return;
    const active = s.toLowerCase() === style;
    btn.classList.toggle('btn-pri', active);
    btn.classList.toggle('btn-ghost', !active);
  });
  const desc = document.getElementById('writingStyleDesc');
  if (desc) desc.textContent = _writingStyleDescs[style] || _writingStyleDescs.flow;
  if (!skipMark) markUnsaved();
}

// ── Widget Style ─────────────────────────────────────────────
function setWidgetStyle(style) {
  _widgetStyle = style;
  document.body.setAttribute('data-widget', style);
  localStorage.setItem('vf-widget', style);

  ['wave','orbit','pulse'].forEach(s => {
    const btn = document.getElementById('wsBtn' + s.charAt(0).toUpperCase() + s.slice(1));
    if (btn) btn.classList.toggle('active', s === style);
  });

  const sb = document.getElementById('sbWidgetStyle');
  if (sb) sb.textContent = style.toUpperCase();
  showToast('AGENT ICON: ' + style.toUpperCase());

  fetch('/api/widget-style', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ style }),
  }).catch(() => {});
}

// ── Target Override ──────────────────────────────────────────
function applyTargetOverrideUI(cat) {
  const sel = document.getElementById('targetOverride');
  if (sel) sel.value = cat || '';
  document.querySelectorAll('#targetCards .mode-card').forEach(c => {
    const isActive = c.dataset.cat === (cat || '');
    c.classList.toggle('active', isActive);
    const indicator = c.querySelector('.mc-active');
    if (indicator) indicator.style.display = isActive ? '' : 'none';
  });
}

function setTargetOverride(cat) {
  applyTargetOverrideUI(cat);
  fetch('/api/targets/override', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category: cat }),
  }).then(() => {
    const label = cat ? (_targetRules[cat]?.label || cat).replace(/[^\w\s]/g, '').trim() : 'AUTO DETECT';
    showToast('TARGET: ' + label.toUpperCase());
  }).catch(() => {});
}

// ── Rules Table ──────────────────────────────────────────────
function loadTargets() {
  fetch('/api/targets')
    .then(r => r.json())
    .then(d => {
      _targetRules = d.targets || {};
      renderRulesTable();
      applyTargetOverrideUI(d.override || '');
      const lbl = document.getElementById('autoDetectedLabel');
      if (lbl) {
        const det = d.detected || 'generic';
        const detLabel = (_targetRules[det]?.label || det).replace(/[^\w\s]/g, '').trim();
        lbl.textContent = d.override ? '' : `(currently: ${detLabel.toUpperCase()})`;
      }
    })
    .catch(() => {});
}

function renderRulesTable() {
  const el = document.getElementById('rulesRows');
  if (!el) return;
  const entries = Object.entries(_targetRules);
  if (!entries.length) {
    el.innerHTML = '<div class="rules-row"><div class="rc" style="grid-column:1/-1;text-align:center;color:var(--vf-muted);font-size:10px;letter-spacing:2px">No rules loaded</div></div>';
    return;
  }
  el.innerHTML = entries.map(([cat, rule]) => {
    const label = (rule.label || cat).replace(/[^\w\s]/g, '').trim();
    return `<div class="rules-row">
      <div class="rc">${escHtml(label)}</div>
      <div class="rc"><span class="tag ${escHtml(cat)}">${escHtml(cat).toUpperCase()}</span></div>
      <div class="rc">${ruleToggleHtml(cat, 'capitalize', !!rule.capitalize)}</div>
      <div class="rc">${ruleToggleHtml(cat, 'add_period', !!rule.add_period)}</div>
    </div>`;
  }).join('');
}

function ruleToggleHtml(cat, field, isOn) {
  return `<div class="tog-wrap" onclick="toggleRule('${cat}','${field}')">
    <div class="tog ${isOn ? 'tog-on' : ''}" id="rt_${cat}_${field}">
      <div class="tog-track"><div class="tog-thumb"></div></div>
    </div>
  </div>`;
}

function toggleRule(cat, field) {
  if (!_targetRules[cat]) return;
  _targetRules[cat][field] = !_targetRules[cat][field];
  const togEl = document.getElementById(`rt_${cat}_${field}`);
  if (togEl) togEl.classList.toggle('tog-on', _targetRules[cat][field]);
  fetch('/api/targets/rule', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category: cat, field, value: _targetRules[cat][field] }),
  }).then(() => showToast('RULE UPDATED')).catch(() => {});
}

// ── Change PIN ───────────────────────────────────────────────
function changePin() {
  const oldPin = document.getElementById('cfgCurrentPin')?.value;
  const newPin = document.getElementById('cfgNewPin')?.value;
  if (!oldPin || !newPin) { showToast('ENTER BOTH PINS'); return; }
  if (newPin.length < 4)  { showToast('PIN TOO SHORT (MIN 4)'); return; }

  fetch('/api/change-pin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old_pin: oldPin, new_pin: newPin }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        showToast('PIN UPDATED');
        document.getElementById('cfgCurrentPin').value = '';
        document.getElementById('cfgNewPin').value = '';
      } else {
        showToast('PIN ERROR: ' + (d.error || 'wrong current pin'));
      }
    })
    .catch(() => showToast('PIN CHANGE FAILED'));
}

// ── Load Devices ─────────────────────────────────────────────
function loadDevices() {
  fetch('/api/devices')
    .then(r => r.json())
    .then(d => {
      const sel = document.getElementById('cfgInputDevice');
      if (!sel || !d.devices) return;
      d.devices.forEach(dev => {
        const opt = document.createElement('option');
        opt.value = dev.index;
        opt.textContent = dev.name;
        sel.appendChild(opt);
      });
    })
    .catch(() => {});
}

// ── Auth ─────────────────────────────────────────────────────
function logout() {
  fetch('/api/logout', { method: 'POST' })
    .then(() => { window.location.href = '/login'; })
    .catch(() => { window.location.href = '/login'; });
}

// ── Refresh All ──────────────────────────────────────────────
function refreshAll() {
  loadStats();
  loadHistory();
  loadConfig();
  loadTargets();
}

// ── Recording Mode Selection ──────────────────────────────────
// Hold and Toggle cards are now informational only (mode is per-hotkey).
// This function manages only the Voice card active state, VAD sliders,
// and wake-phrase row — all driven by WAKE_WORD_ENABLED.
function setRecordingMode(mode) {
  const voiceOn = _cfgToggles.WAKE_WORD_ENABLED;

  // Voice card active state only
  document.querySelectorAll('#recordingModePills .rec-mode-card').forEach(b => {
    if (b.dataset.mode === 'voice') {
      b.classList.toggle('active', voiceOn);
      const ind = b.querySelector('.rmc-active');
      if (ind) ind.style.display = voiceOn ? '' : 'none';
    } else {
      b.classList.remove('active');
      const ind = b.querySelector('.rmc-active');
      if (ind) ind.style.display = 'none';
    }
  });

  const wakeRow = document.getElementById('wakePhraseRow');
  if (wakeRow) wakeRow.classList.toggle('voice-active', voiceOn);

  const vadSilenceRow   = document.getElementById('vadSilenceRow');
  const vadThresholdRow = document.getElementById('vadThresholdRow');
  const vadDisplay = voiceOn ? '' : 'none';
  if (vadSilenceRow)   vadSilenceRow.style.display   = vadDisplay;
  if (vadThresholdRow) vadThresholdRow.style.display  = vadDisplay;
  updateMicLevelPolling();
  loadEnrollmentStatus();

  const el = document.querySelector('.status-sub');
  if (el) el.textContent = voiceOn
    ? 'SAY WAKE PHRASE → SPEAK → SILENCE STOPS → TEXT PASTES'
    : 'CONFIGURED HOTKEYS ACTIVE — SPEAK AND TEXT PASTES AT CURSOR';
}

// Sync the two wake-word inputs (inline + legacy hidden)
function syncWakeWordInputs(val) {
  const legacy = document.getElementById('cfgWakeWord');
  if (legacy) legacy.value = val;
}

// Test whether a phrase would trigger the wake word — no restart needed
async function testWakePhrase() {
  const input  = document.getElementById('cfgWakeTestInput');
  const result = document.getElementById('wakeTestResult');
  if (!input || !result) return;
  const text = input.value.trim();
  if (!text) { result.textContent = 'Enter a phrase to test.'; result.style.color = 'var(--vf-muted)'; return; }
  result.textContent = 'Testing…'; result.style.color = 'var(--vf-muted)';
  try {
    const r = await fetch('/api/test-wake-phrase?text=' + encodeURIComponent(text));
    const d = await r.json();
    if (d.matched) {
      result.innerHTML = `<span style="color:var(--vf-cyan)">✅ Match</span> — Whisper said <b>${escHtml(text)}</b>, wake word <b>${escHtml(d.wake_word)}</b> confirmed${d.via_alt ? ' via alternative "' + escHtml(d.via_alt) + '"' : ''}${d.remaining ? ' · remaining: "' + escHtml(d.remaining) + '"' : ''}`;
    } else {
      result.innerHTML = `<span style="color:#ff6b6b">❌ No match</span> — <b>${escHtml(text)}</b> would NOT trigger <b>${escHtml(d.wake_word)}</b>`;
    }
  } catch(e) {
    result.textContent = 'Test failed: ' + e;
    result.style.color = '#ff6b6b';
  }
}
function escHtml(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// Populate per-mode hotkey chips inside the mode cards using global _hotkeys
function _renderModeHotkeys() {
  const fmt = c => c.replace(/<([^>]+)>/g, (_, k) => k.toUpperCase()).replace(/\+/g, ' + ');
  ['hold', 'toggle'].forEach(mode => {
    const suffix = mode.charAt(0).toUpperCase() + mode.slice(1);
    const el = document.getElementById(`rmcHotkeys${suffix}`);
    if (!el) return;
    const assigned = _hotkeys.filter(h => (h.recordMode || 'hold') === mode && h.value);
    el.innerHTML = assigned.length
      ? assigned.map(h => `<span class="rmc-key">${fmt(h.value)}</span>`).join('')
      : '<span style="font-size:9px;color:var(--vf-muted);opacity:0.6">— none assigned —</span>';
  });
  const voiceEl = document.getElementById('rmcHotkeysVoice');
  if (voiceEl) {
    const allChips = _hotkeys.filter(h => h.value)
      .map(h => `<span class="rmc-key">${fmt(h.value)}</span>`).join('');
    voiceEl.innerHTML = (allChips || '') +
      '<span style="display:block;font-size:9px;color:var(--vf-muted);margin-top:4px;letter-spacing:1px">OR say wake phrase</span>';
  }
}

// ── AI Backend Selection ──────────────────────────────────────
// ── Quick-add: all API keys in one panel ─────────────────────
// provider -> [configKey, kind(asr|llm), label, placeholder, maskedField]
const _QUICK_KEYS = {
  groq:      ['GROQ_API_KEY',      'asr', 'Groq (ASR + LLM)', 'gsk_…',  'GROQ_API_KEY_MASKED'],
  deepgram:  ['DEEPGRAM_API_KEY',  'asr', 'Deepgram (ASR)',   'Token …','DEEPGRAM_API_KEY_MASKED'],
  openai:    ['OPENAI_API_KEY',    'asr', 'OpenAI (ASR + LLM)','sk-…',   'OPENAI_API_KEY_MASKED'],
  cerebras:  ['CEREBRAS_API_KEY',  'llm', 'Cerebras (LLM)',   'csk-…',  'CEREBRAS_API_KEY_MASKED'],
  gemini:    ['GEMINI_API_KEY',    'llm', 'Gemini (LLM)',     'AIza…',  'GEMINI_API_KEY_MASKED'],
  anthropic: ['ANTHROPIC_API_KEY', 'llm', 'Anthropic (LLM)',  'sk-ant-…','ANTHROPIC_API_KEY_MASKED'],
  nvidia:    ['NVIDIA_API_KEY',    'llm', 'NVIDIA (LLM only)', 'nvapi-…','NVIDIA_API_KEY_MASKED'],
};

function buildQuickKeys(d) {
  const grid = document.getElementById('quickKeysGrid');
  if (!grid) return;
  grid.innerHTML = Object.entries(_QUICK_KEYS).map(([p, [, , label, ph, maskField]]) => {
    const masked = d && d[maskField] ? 'Current: ' + d[maskField] : ph;
    return `<div style="display:flex;gap:8px;align-items:center">
      <span style="font-size:10px;color:var(--vf-muted);min-width:128px">${label}</span>
      <input class="cfg-in" id="qk_${p}" type="password" placeholder="${masked}"
             style="flex:1" oninput="markUnsaved()">
      <button class="btn-ghost" onclick="quickTestKey('${p}')"
              style="white-space:nowrap;padding:7px 10px;font-size:9px;letter-spacing:1px">TEST</button>
      <span id="qks_${p}" style="font-size:10px;min-width:64px;color:var(--vf-muted)"></span>
    </div>`;
  }).join('');
}

function quickTestKey(provider) {
  const meta = _QUICK_KEYS[provider];
  if (!meta) return;
  const [cfgKey, kind] = meta;
  const input = document.getElementById('qk_' + provider);
  const stat  = document.getElementById('qks_' + provider);
  const val   = (input?.value || '').trim();
  if (stat) { stat.textContent = '⟳ …'; stat.style.color = 'var(--vf-muted)'; }

  // Save the typed key (if any), then validate THIS provider explicitly.
  const saveBody = {};
  if (val) saveBody[cfgKey] = val;
  fetch('/api/config', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(saveBody),
  })
    .then(() => {
      const url  = kind === 'asr' ? '/api/test-api-key' : '/api/test-llm';
      const body = kind === 'asr' ? { key: val, backend: provider } : { backend: provider };
      return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    })
    .then(r => r.json())
    .then(dd => {
      if (!stat) return;
      if (dd.ok) {
        stat.textContent = '✓ valid';
        stat.style.color = 'var(--vf-cyan)';
        showToast(provider.toUpperCase() + ' KEY VALID ✓');
      } else {
        stat.textContent = '✕ ' + (dd.error || 'invalid').substring(0, 36);
        stat.style.color = 'var(--vf-red)';
        showToast(provider.toUpperCase() + ': ' + (dd.error || 'invalid').substring(0, 40));
      }
    })
    .catch(() => { if (stat) { stat.textContent = '✕ failed'; stat.style.color = 'var(--vf-red)'; } });
}

function setAsrBackend(backend) {
  _asrBackend = backend;
  document.querySelectorAll('#asrBackendPills .backend-pill').forEach(b => {
    b.classList.toggle('active', b.dataset.backend === backend);
  });
  const sections = ['groq', 'deepgram', 'nvidia', 'openai', 'local', 'custom'];
  sections.forEach(s => {
    document.querySelectorAll(`.asr-${s}-fields`).forEach(el => {
      el.style.display = s === backend ? '' : 'none';
    });
  });
  markUnsaved();
}

function setLlmBackend(backend) {
  _llmBackend = backend;
  document.querySelectorAll('#llmBackendPills .backend-pill').forEach(b => {
    b.classList.toggle('active', b.dataset.backend === backend);
  });
  const sections = ['nvidia', 'groq', 'cerebras', 'gemini', 'openai', 'ollama', 'anthropic', 'custom'];
  sections.forEach(s => {
    document.querySelectorAll(`.llm-${s}-fields`).forEach(el => {
      el.style.display = s === backend ? '' : 'none';
    });
  });
  markUnsaved();
}

// ── Test ASR ──────────────────────────────────────────────────
function testAsr() {
  const status = document.getElementById('cfgApiStatus');
  if (status) { status.textContent = '⟳ Saving key…'; status.style.color = 'var(--vf-muted)'; }

  // Save whatever is typed before running the transcription test — the test
  // endpoint reads from the saved config, so an unsaved key would silently
  // test with an empty/stale value and return a confusing 401.
  _saveTypedKeyNow().then(() => {
    if (status) { status.textContent = '⟳ Testing ASR…'; status.style.color = 'var(--vf-muted)'; }
    return fetch('/api/transcribe-test', { method: 'POST' });
  })
    .then(r => r.json())
    .then(d => {
      if (status) {
        if (d.ok) {
          status.textContent = '✓ ASR endpoint reachable (' + _asrBackend + ')';
          status.style.color = 'var(--vf-cyan)';
          showToast('ASR TEST PASSED ✓');
        } else {
          status.textContent = '✕ ' + (d.error || 'ASR test failed');
          status.style.color = 'var(--vf-red)';
          showToast('ASR TEST FAILED');
        }
      }
    })
    .catch(() => {
      if (status) { status.textContent = '✕ Connection error'; status.style.color = 'var(--vf-red)'; }
    });
}

// ── Backup Modal ──────────────────────────────────────────────
function openBackupModal() {
  const modal = document.getElementById('backupModal');
  if (!modal) return;
  modal.style.display = 'flex';

  const info = document.getElementById('backupInfo');
  if (info) info.textContent = 'Loading backup info…';

  fetch('/api/backup-info')
    .then(r => r.json())
    .then(d => {
      if (info) {
        info.innerHTML = `<b>${d.db_sessions || 0}</b> sessions · <b>${d.total_size_fmt}</b> total · <span style="color:var(--vf-muted)">${d.dir}</span>`;
      }
      const list = document.getElementById('bkFileList');
      if (list && d.files) {
        list.innerHTML = d.files.map(f =>
          `<div class="bk-file-row">
            <div class="bk-file-type">${escHtml(f.type.toUpperCase())}</div>
            <div class="bk-file-name" title="${escHtml(f.name)}">${escHtml(f.name)}</div>
            <div class="bk-file-size">${(f.size/1024).toFixed(1)}KB</div>
          </div>`
        ).join('') || '<div style="color:var(--vf-muted)">No files found</div>';
      }
    })
    .catch(() => { if (info) info.textContent = 'Could not load backup info.'; });
}

function closeBackupModal() {
  const modal = document.getElementById('backupModal');
  if (modal) modal.style.display = 'none';
}

function downloadBackup() {
  const cfg  = document.getElementById('bkConfig')?.checked ? '1' : '0';
  const db   = document.getElementById('bkDb')?.checked     ? '1' : '0';
  const logs = document.getElementById('bkLogs')?.checked   ? '1' : '0';
  if (cfg === '0' && db === '0' && logs === '0') {
    showToast('SELECT AT LEAST ONE ITEM');
    return;
  }
  const url = `/api/backup?config=${cfg}&db=${db}&logs=${logs}`;
  window.location.href = url;
  showToast('BACKUP DOWNLOADING…');
  setTimeout(closeBackupModal, 1200);
}

// ── Diagnostics bundle (scrubbed — no API keys) ──────────────
function downloadDebugBundle() {
  window.location.href = '/api/debug-bundle';
  showToast('DEBUG BUNDLE DOWNLOADING…');
}

// ── Transcript quality score (0–100) ─────────────────────────
// 95+ = excellent (LLM-cleaned, proper punctuation, no fillers)
// 80–94 = good   70–79 = fair   <70 = poor
const _FILLER_RE = /\b(uh+|um+|like|you know|basically|literally|so|right|yeah|okay|hmm+)\b/gi;

function _qualityScore(raw, clean) {
  if (!clean || clean.length < 3) return 0;
  const cleanWords   = (clean || '').split(/\s+/).filter(Boolean).length;
  const cleanFillers = (clean.match(_FILLER_RE) || []).length;
  const fillerRate   = cleanWords > 0 ? cleanFillers / cleanWords : 0;
  const hasPunct     = /[.!?]$/.test(clean.trim());
  const hasCapital   = /^[A-Z]/.test(clean.trim());

  let score = 100;
  if (!hasPunct)   score -= 8;   // missing terminal punctuation
  if (!hasCapital) score -= 2;   // missing capitalisation
  score -= Math.min(20, Math.round(fillerRate * 200));   // filler penalty (capped −20)
  return Math.max(0, Math.min(100, score));
}

// ── Backend status panel ──────────────────────────────────────
function checkBackendStatus() {
  const panel = document.getElementById('backendStatusPanel');
  if (panel) panel.innerHTML = '<span style="color:var(--vf-muted)">Testing backends…</span>';

  fetch('/api/backend-status')
    .then(r => r.json())
    .then(d => {
      if (!panel) return;
      const rows = Object.entries(d).map(([name, res]) => {
        const ok    = res.ok;
        const color = ok ? 'var(--vf-cyan)' : 'var(--vf-red)';
        const label = ok ? '● OK' : '✕ ' + String(res.status || 'ERR').toUpperCase();
        return `<div class="bs-row">
          <span class="bs-name">${name.toUpperCase()}</span>
          <span class="bs-status" style="color:${color}">${label}</span>
        </div>`;
      }).join('');
      panel.innerHTML = rows || '<span style="color:var(--vf-muted)">No backends configured</span>';
    })
    .catch(() => { if (panel) panel.innerHTML = '<span style="color:var(--vf-red)">Test failed</span>'; });
}

// ── Windows auto-start ────────────────────────────────────────
let _autostartMinimized = false;

function loadAutostart() {
  fetch('/api/autostart')
    .then(r => r.json())
    .then(d => {
      if (!d.ok) return;
      document.getElementById('togAutostart')?.querySelector('.tog')?.classList.toggle('tog-on', d.enabled);
      _autostartMinimized = !!d.minimized;
      document.getElementById('togAutostartMinimized')?.querySelector('.tog')?.classList.toggle('tog-on', _autostartMinimized);
    })
    .catch(() => {});
}

function toggleAutostart() {
  const tog = document.getElementById('togAutostart');
  const currently = tog?.querySelector('.tog')?.classList.contains('tog-on');
  fetch('/api/autostart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enable: !currently, minimized: _autostartMinimized }),
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) {
      tog?.querySelector('.tog')?.classList.toggle('tog-on', d.enabled);
      showToast(d.enabled ? 'AUTO-START ENABLED' : 'AUTO-START DISABLED');
    } else {
      showToast('AUTO-START: ' + (d.error || 'FAILED — Windows only'));
    }
  })
  .catch(() => showToast('AUTO-START FAILED'));
}

function toggleAutostartMinimized() {
  _autostartMinimized = !_autostartMinimized;
  document.getElementById('togAutostartMinimized')?.querySelector('.tog')?.classList.toggle('tog-on', _autostartMinimized);
  // Re-apply to registry if autostart is currently enabled
  const enabled = document.getElementById('togAutostart')?.querySelector('.tog')?.classList.contains('tog-on');
  if (enabled) {
    fetch('/api/autostart', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enable: true, minimized: _autostartMinimized }),
    }).catch(() => {});
  }
  showToast(_autostartMinimized ? 'WILL START MINIMIZED' : 'WILL START WITH UI');
}

// ── Voice enrollment ─────────────────────────────────────────
function loadEnrollmentStatus() {
  fetch('/api/enroll-voice')
    .then(r => r.json())
    .then(d => {
      const row      = document.getElementById('voiceEnrollRow');
      const status   = document.getElementById('enrollStatus');
      const clearBtn = document.getElementById('enrollClearBtn');
      if (!row) return;

      // Show enrollment row only when wake word mode is on
      row.style.display = _cfgToggles.WAKE_WORD_ENABLED ? '' : 'none';

      if (d.enrolled) {
        status.textContent   = `Voice enrolled (${d.sample_count} samples) — only your voice activates the phrase`;
        status.style.color   = 'var(--vf-cyan)';
        if (clearBtn) clearBtn.style.display = '';
      } else {
        status.textContent   = 'Not enrolled — anyone can trigger the wake phrase';
        status.style.color   = 'var(--vf-muted)';
        if (clearBtn) clearBtn.style.display = 'none';
      }
    })
    .catch(() => {});
}

// ── Voice enrollment wizard ───────────────────────────────────
// Uses browser MediaRecorder so NO hotkey is needed.
// The user clicks RECORD SAMPLE → browser records 2.5 s → sends to backend.

let _enrollCollected = 0;
let _enrollNeeded    = 3;
let _enrollRecording = false;
let _enrollStream    = null;

function startEnrollment() {
  fetch('/api/enroll-voice/start', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) { showToast('ENROLL ERROR: ' + (d.error || 'No wake phrase set')); return; }
      _enrollCollected = 0;
      _enrollNeeded    = d.needed || 3;
      document.getElementById('enrollActions').style.display = 'none';
      const wiz = document.getElementById('enrollWizard');
      if (wiz) wiz.style.display = '';
      const pl = document.getElementById('enrollPhraseLabel');
      if (pl) pl.textContent = `"${d.phrase}"`;
      _enrollUpdateDots(0);
      document.getElementById('enrollMsg').textContent = 'Click RECORD SAMPLE, then say the phrase';
    })
    .catch(() => showToast('ENROLLMENT START FAILED'));
}

function cancelEnrollment() {
  document.getElementById('enrollWizard').style.display = 'none';
  document.getElementById('enrollActions').style.display = '';
  document.getElementById('enrollMicRow').style.display = 'none';
  _enrollStream = null;
}

async function enrollRecordSample() {
  if (_enrollRecording) return;
  _enrollRecording = true;
  const btn = document.getElementById('enrollRecordBtn');
  const micRow = document.getElementById('enrollMicRow');
  const cd     = document.getElementById('enrollCountdown');
  const bar    = document.getElementById('enrollMicBar');
  if (btn) { btn.disabled = true; btn.textContent = '[ RECORDING... ]'; }
  if (micRow) micRow.style.display = '';

  const reset = () => {
    _enrollRecording = false;
    if (btn) { btn.disabled = false; btn.textContent = '[ RECORD SAMPLE ]'; }
    if (micRow) micRow.style.display = 'none';
    if (cd) cd.textContent = '2.5s';
  };

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    _enrollStream = stream;

    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') await ctx.resume();
    const src  = ctx.createMediaStreamSource(stream);
    const anal = ctx.createAnalyser();
    anal.fftSize = 256;
    src.connect(anal);

    // Raw PCM capture — avoids MediaRecorder/webm so no ffmpeg dependency
    // and no async race on the final chunk.
    const processor = ctx.createScriptProcessor(4096, 1, 1);
    const silence    = ctx.createGain();
    silence.gain.value = 0;
    const pcmChunks = [];
    processor.onaudioprocess = e => pcmChunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    src.connect(processor);
    processor.connect(silence);
    silence.connect(ctx.destination);

    // Mic level visualiser during recording
    const data = new Uint8Array(anal.frequencyBinCount);
    const micTick = () => {
      if (!_enrollRecording) return;
      anal.getByteFrequencyData(data);
      const rms = data.reduce((s, v) => s + v, 0) / data.length;
      if (bar) bar.style.width = Math.min(100, rms * 1.5) + '%';
      requestAnimationFrame(micTick);
    };
    requestAnimationFrame(micTick);

    // Countdown display
    let timeLeft = 2.5;
    const cdInterval = setInterval(() => {
      timeLeft -= 0.1;
      if (cd) cd.textContent = Math.max(0, timeLeft).toFixed(1) + 's';
    }, 100);

    await new Promise(r => setTimeout(r, 2500));
    clearInterval(cdInterval);

    processor.disconnect();
    src.disconnect();
    silence.disconnect();
    stream.getTracks().forEach(t => t.stop());

    const sampleRate = ctx.sampleRate;
    const total = pcmChunks.reduce((s, c) => s + c.length, 0);
    await ctx.close();

    if (total < sampleRate * 0.2) {
      document.getElementById('enrollMsg').textContent =
        'No audio captured — check microphone permissions and try again';
      showToast('NO AUDIO CAPTURED — CHECK MIC PERMISSIONS');
      reset();
      return;
    }

    const samples = new Float32Array(total);
    let off = 0;
    for (const c of pcmChunks) { samples.set(c, off); off += c.length; }

    const wavBlob  = _enrollEncodeWav(samples, sampleRate);
    const arrayBuf = await wavBlob.arrayBuffer();
    const b64      = _bytesToBase64(new Uint8Array(arrayBuf));

    const resp = await fetch('/api/enroll-voice/feed', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wav_b64: b64, mime: 'audio/wav' }),
    }).then(r => r.json());

    if (resp.accepted) {
      _enrollCollected = resp.collected;
      _enrollUpdateDots(_enrollCollected);
      if (resp.done) {
        document.getElementById('enrollMsg').textContent = 'Enrollment complete!';
        showToast('VOICE ENROLLED — only your voice triggers the wake phrase');
        setTimeout(() => { cancelEnrollment(); loadEnrollmentStatus(); }, 1500);
      } else {
        document.getElementById('enrollMsg').textContent =
          `Sample ${resp.collected}/${resp.needed} saved — click RECORD SAMPLE again`;
      }
    } else {
      const reason = resp.message || resp.error || 'Sample rejected — try again';
      document.getElementById('enrollMsg').textContent = reason;
      showToast('SAMPLE REJECTED: ' + reason);
    }
  } catch (err) {
    const isPermissionError = err && (err.name === 'NotAllowedError' || err.name === 'NotFoundError');
    const reason = isPermissionError
      ? 'Microphone access denied — check browser/site permissions and try again'
      : 'Error: ' + (err && err.message ? err.message : err);
    showToast('MIC ERROR: ' + (err && err.message ? err.message : err));
    document.getElementById('enrollMsg').textContent = reason;
  }

  reset();
}

// Convert a Uint8Array to base64 without spreading it into function
// arguments — String.fromCharCode(...bytes) overflows the call stack for
// anything beyond a few tens of KB (a 2.5s WAV is ~220KB).
function _bytesToBase64(bytes) {
  let binary = '';
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

// Encode mono Float32 PCM samples as a 16-bit PCM WAV blob.
function _enrollEncodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view   = new DataView(buffer);
  const writeStr = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);              // PCM
  view.setUint16(22, 1, true);              // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true);              // block align
  view.setUint16(34, 16, true);             // bits per sample
  writeStr(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return new Blob([view], { type: 'audio/wav' });
}

function _enrollUpdateDots(collected) {
  const ids = ['enrollDot1', 'enrollDot2', 'enrollDot3'];
  ids.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (i < collected) {
      el.style.borderColor = 'var(--vf-cyan)';
      el.style.color       = 'var(--vf-cyan)';
      el.style.background  = 'rgba(37,255,224,0.08)';
      el.textContent       = '✓';
    } else if (i === collected) {
      el.style.borderColor = 'var(--vf-pri)';
      el.style.color       = 'var(--vf-pri)';
      el.style.background  = 'transparent';
      el.textContent       = String(i + 1);
    } else {
      el.style.borderColor = 'var(--vf-b1)';
      el.style.color       = 'var(--vf-muted)';
      el.style.background  = 'transparent';
      el.textContent       = String(i + 1);
    }
  });
}

function clearEnrollment() {
  if (!confirm('Delete voice profile? Anyone can trigger the wake phrase again.')) return;
  fetch('/api/enroll-voice/clear', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (d.ok) { loadEnrollmentStatus(); showToast('VOICE PROFILE CLEARED'); }
      else showToast('CLEAR FAILED: ' + (d.error || ''));
    })
    .catch(() => showToast('CLEAR FAILED'));
}

// (legacy polling — kept as fallback if browser mic unavailable)
function pollEnrollment() {
  fetch('/api/enroll-voice').then(r => r.json()).then(d => {
    if (d.enrolled) { loadEnrollmentStatus(); showToast('VOICE ENROLLED'); }
    else if (d.session_active) setTimeout(pollEnrollment, 1500);
  }).catch(() => {});
}

// ── Mic-level polling (for VAD calibration) ──────────────────
let _micLevelTimer = null;

function startMicLevelPolling() {
  if (_micLevelTimer) return;
  _micLevelTimer = setInterval(() => {
    fetch('/api/mic-level')
      .then(r => r.json())
      .then(d => {
        const bar = document.getElementById('micLevelBar');
        const val = document.getElementById('micLevelVal');
        if (!bar || !val) { stopMicLevelPolling(); return; }
        const pct = Math.min(100, (d.rms / 0.15) * 100);
        bar.style.width = pct + '%';
        bar.style.background = d.above ? 'var(--vf-cyan)' : 'var(--vf-pri)';
        val.textContent = d.rms.toFixed(3);
      })
      .catch(() => stopMicLevelPolling());
  }, 100);
}

function stopMicLevelPolling() {
  if (_micLevelTimer) { clearInterval(_micLevelTimer); _micLevelTimer = null; }
}

// Start polling when the VAD threshold row is visible (wake word enabled)
function updateMicLevelPolling() {
  const row = document.getElementById('vadThresholdRow');
  if (row && row.style.display !== 'none') {
    startMicLevelPolling();
  } else {
    stopMicLevelPolling();
  }
}

function calibrateVad() {
  fetch('/api/calibrate-vad', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        const tl = document.getElementById('cfgVadThreshold');
        const tv = document.getElementById('cfgVadThresholdVal');
        if (tl) tl.value = d.threshold;
        if (tv) tv.textContent = d.threshold.toFixed(3);
        markUnsaved();
        showToast(`VAD THRESHOLD → ${d.threshold.toFixed(3)} (voice RMS ${d.voice_rms})`);
      } else {
        showToast('CALIBRATE: ' + (d.error || 'FAILED'));
      }
    })
    .catch(() => showToast('CALIBRATE FAILED'));
}

// ── Purge old history ─────────────────────────────────────────
function purgeOldHistory() {
  const days = parseInt(document.getElementById('cfgPurgeDays')?.value) || 30;
  if (!confirm(`Delete all transcriptions older than ${days} days?`)) return;
  fetch('/api/history/purge-old', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ days }),
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) showToast(`DELETED ${d.deleted} ENTRIES`);
    else showToast('PURGE FAILED: ' + (d.error || ''));
    refreshAll();
  })
  .catch(() => showToast('PURGE FAILED'));
}

// ── LLM Streaming (SSE) ───────────────────────────────────────
let _sseSource      = null;
let _sseBuffer      = '';
let _sseActive      = false;
let _sseTimeoutId   = null;
const _SSE_TIMEOUT_MS = 120_000;   // 2 min — kill stream if LLM stalls

function startLlmStream() {
  if (_sseActive) return;
  _sseActive = true;
  _sseBuffer = '';

  const liveEl = document.getElementById('liveStreamText');
  const liveBox = document.getElementById('liveStreamBox');
  if (liveBox) { liveBox.style.display = ''; liveBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
  if (liveEl) liveEl.textContent = '';

  _sseSource = new EventSource('/api/llm-stream');

  _sseTimeoutId = setTimeout(() => stopLlmStream(false), _SSE_TIMEOUT_MS);

  _sseSource.onmessage = e => {
    if (e.data === '[DONE]') {
      stopLlmStream(true);
      return;
    }
    try {
      const d = JSON.parse(e.data);
      if (d.t) {
        _sseBuffer += d.t;
        if (liveEl) liveEl.textContent = _sseBuffer;
      }
    } catch (_) {}
  };

  _sseSource.onerror = () => stopLlmStream(false);
}

function stopLlmStream(success) {
  _sseActive = false;
  clearTimeout(_sseTimeoutId); _sseTimeoutId = null;
  if (_sseSource) { _sseSource.close(); _sseSource = null; }
  const liveBox = document.getElementById('liveStreamBox');

  if (success && _sseBuffer) {
    // Keep box visible briefly, then fade
    setTimeout(() => { if (liveBox) liveBox.style.display = 'none'; }, 4000);
  } else {
    if (liveBox) liveBox.style.display = 'none';
  }
  _sseBuffer = '';
}

// ── State poller: watch for 'processing' to auto-open stream ──
let _lastState = 'idle';
function pollState() {
  fetch('/api/state')
    .then(r => r.json())
    .then(d => {
      const s = d.state || 'idle';
      if (s === 'processing' && _lastState !== 'processing') {
        startLlmStream();
      }
      if (s !== 'processing' && _lastState === 'processing' && !_sseActive) {
        const liveBox = document.getElementById('liveStreamBox');
        if (liveBox) liveBox.style.display = 'none';
      }
      _lastState = s;

      // Auto-update notification (shown once)
      if (d.update_available && !_updateToastShown) {
        _updateToastShown = true;
        showToast(`UPDATE AVAILABLE: v${d.update_available} — github.com/NibCast/nibcast/releases`);
        const hdr = document.querySelector('.vf-hdr-right');
        if (hdr) {
          const badge = document.createElement('a');
          badge.href = 'https://github.com/NibCast/nibcast/releases';
          badge.target = '_blank';
          badge.style.cssText = 'font-size:9px;letter-spacing:1px;color:var(--vf-pri);border:1px solid var(--vf-pri);padding:3px 8px;text-decoration:none;border-radius:2px;flex-shrink:0';
          badge.textContent = `v${d.update_available} AVAILABLE`;
          hdr.prepend(badge);
        }
      }

      // Update header status indicator
      const dot  = document.querySelector('.vf-dot');
      const span = document.getElementById('headerStatus');
      const labels = { idle: 'ARMED', recording: 'REC', processing: 'PROC', error: 'ERR' };
      if (span) span.textContent = labels[s] || s.toUpperCase();
      if (dot) {
        dot.style.background = s === 'recording' ? 'var(--vf-red)'
          : s === 'processing' ? 'var(--vf-cyan)'
          : s === 'error'      ? '#ff3838'
          : 'var(--vf-pri)';
      }
    })
    .catch(() => {});
}

window.addEventListener('DOMContentLoaded', () => {
  setInterval(pollState, 1500);
  loadAutostart();
});

// ── Enhanced Insights ─────────────────────────────────────────
const _STOP_WORDS = new Set([
  'the','a','an','and','or','but','in','on','at','to','for','of','with','is','it',
  'that','this','was','are','be','have','has','had','i','you','he','she','we','they',
  'do','did','will','would','can','could','should','may','might','not','if','as',
  'by','from','about','which','what','so','just','out','up','my','your','their',
  'our','all','also','more','how','when','who','into','than','then','its','been',
  'there','were','his','her','am','get','got','much','very','no','yes',
]);

const _FILLERS = new Set([
  'uh','um','like','you know','you know what i mean','i mean','basically',
  'literally','actually','totally','honestly','whatever','kind of','sort of',
  'right','okay','so yeah','well','hmm','err',
]);

function renderInsightsExtra() {
  if (!_history.length) return;

  const allText = _history.map(x => (x.clean_text || x.raw_text || '').toLowerCase());
  const allWords = _history.map(x => wordCount(x.clean_text || x.raw_text || ''));
  const totalWords = allWords.reduce((a, b) => a + b, 0);
  const el = id => document.getElementById(id);

  if (el('insLongestSession')) el('insLongestSession').textContent = Math.max(...allWords) + ' words';
  if (el('insTotalWords')) {
    el('insTotalWords').textContent = totalWords > 999 ? (totalWords/1000).toFixed(1)+'k' : String(totalWords);
  }

  const durations = _history.filter(x => x.duration_sec).map(x => x.duration_sec);
  const avgDur = durations.length ? (durations.reduce((a,b)=>a+b,0)/durations.length).toFixed(1) : '—';
  if (el('insAvgDuration')) el('insAvgDuration').textContent = avgDur + 's';

  const weekAgo = Date.now() - 7*24*60*60*1000;
  const weekCount = _history.filter(x => x.created_at && new Date(x.created_at).getTime() > weekAgo).length;
  if (el('insThisWeek')) el('insThisWeek').textContent = weekCount;

  // ── Word frequency (exclude stop words) ──────────────────────
  const wordFreq = {};
  let totalTokens = 0;
  let fillerCount = 0;
  let uniqueSet   = new Set();
  allText.forEach(t => {
    const tokens = t.match(/\b[a-z']+\b/g) || [];
    tokens.forEach(w => {
      totalTokens++;
      uniqueSet.add(w);
      if (_FILLERS.has(w)) fillerCount++;
      if (!_STOP_WORDS.has(w) && w.length > 2) {
        wordFreq[w] = (wordFreq[w] || 0) + 1;
      }
    });
  });

  // Vocabulary richness (type-token ratio)
  const richness = totalTokens > 0 ? Math.round((uniqueSet.size / totalTokens) * 100) : 0;
  if (el('insVocabRichness')) el('insVocabRichness').textContent = richness + '%';

  // Filler rate
  const fillerRate = totalTokens > 0 ? ((fillerCount / totalTokens) * 100).toFixed(1) : '0.0';
  if (el('insFillerRate')) el('insFillerRate').textContent = fillerRate + '%';

  // Tone classification
  const fr = parseFloat(fillerRate);
  const tone = fr < 2 ? 'FORMAL' : fr < 5 ? 'NATURAL' : fr < 10 ? 'CASUAL' : 'VERY CASUAL';
  const toneColor = fr < 2 ? 'var(--vf-cyan)' : fr < 5 ? 'var(--vf-pri)' : fr < 10 ? 'var(--vf-orange)' : 'var(--vf-red)';
  if (el('insTone')) {
    el('insTone').textContent = tone;
    el('insTone').style.color = toneColor;
  }

  // Average sentence length
  const allSentences = allText.join(' ').split(/[.!?]+/).filter(s => s.trim().length > 0);
  const avgSentWords = allSentences.length
    ? Math.round(allSentences.map(s => (s.match(/\b\w+\b/g)||[]).length).reduce((a,b)=>a+b,0) / allSentences.length)
    : 0;
  if (el('insAvgSentence')) el('insAvgSentence').textContent = avgSentWords || '—';

  // Top 10 content words
  const topWords = Object.entries(wordFreq).sort((a,b)=>b[1]-a[1]).slice(0, 10);
  const twEl = document.getElementById('topWordsList');
  if (twEl) {
    if (!topWords.length) {
      twEl.innerHTML = '<div class="empty">No data yet</div>';
    } else {
      const maxF = topWords[0][1];
      twEl.innerHTML = topWords.map(([word, freq]) => {
        const pct = Math.round((freq / maxF) * 100);
        return `<div class="app-bar-row">
          <div class="app-bar-name" style="font-style:italic;color:var(--vf-text)">${escHtml(word)}</div>
          <div class="app-bar-track"><div class="app-bar-fill" style="width:${pct}%"></div></div>
          <div class="app-bar-count">${freq}×</div>
        </div>`;
      }).join('');
    }
  }
}

// ── Snippets ──────────────────────────────────────────────────
let _snippets = {};

function renderSnippets() {
  const el = document.getElementById('snippetList');
  if (!el) return;
  const entries = Object.entries(_snippets);
  if (!entries.length) {
    el.innerHTML = '<div style="font-size:10px;color:var(--vf-muted);padding:4px 0">No snippets yet.</div>';
    return;
  }
  el.innerHTML = entries.map(([phrase, expansion]) => `
    <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--vf-b0)">
      <span style="font-size:11px;color:var(--vf-pri);min-width:120px;flex-shrink:0">"${escHtml(phrase)}"</span>
      <span style="font-size:10px;color:var(--vf-muted)">→</span>
      <span style="font-size:11px;color:var(--vf-text);flex:1;word-break:break-all">${escHtml(expansion)}</span>
      <button class="del-btn" onclick="deleteSnippet(${JSON.stringify(phrase)})" title="Delete">✕</button>
    </div>`).join('');
}

function addSnippet() {
  const phraseEl = document.getElementById('snipPhrase');
  const expandEl = document.getElementById('snipExpand');
  const phrase    = (phraseEl?.value || '').trim().toLowerCase();
  const expansion = (expandEl?.value || '').trim();
  if (!phrase) { if (phraseEl) phraseEl.style.borderColor = 'var(--vf-red, #f87)'; return; }
  if (!expansion) { if (expandEl) expandEl.style.borderColor = 'var(--vf-red, #f87)'; return; }
  _snippets[phrase] = expansion;
  if (phraseEl) phraseEl.value = '';
  if (expandEl) expandEl.value = '';
  renderSnippets();
  markUnsaved();
  showToast(`SNIPPET ADDED: "${phrase}"`);
}

function deleteSnippet(phrase) {
  delete _snippets[phrase];
  renderSnippets();
  markUnsaved();
}

// ── First-run detection ───────────────────────────────────────
(function checkFirstRun() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('firstrun') === '1') {
    const banner = document.getElementById('firstRunBanner');
    if (banner) banner.style.display = '';
    // Switch to config tab so the API key field is visible immediately
    setTimeout(() => switchPanel('config'), 100);
    // Clean the URL without reloading
    history.replaceState({}, '', '/');
  }
})();

function dismissFirstRun() {
  const banner = document.getElementById('firstRunBanner');
  if (banner) banner.style.display = 'none';
}

function scrollToGroqKey() {
  const row = document.getElementById('groqKeyRow');
  if (row) {
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const input = document.getElementById('cfgGroqKey');
    if (input) setTimeout(() => input.focus(), 400);
  }
}

function createDesktopShortcut(btnId) {
  const btn = document.getElementById(btnId || 'createShortcutBtn');
  if (btn) { btn.textContent = '[ CREATING... ]'; btn.disabled = true; }
  fetch('/api/create-shortcut', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        showToast('DESKTOP SHORTCUT CREATED');
        if (btn) btn.textContent = '[ SHORTCUT CREATED ]';
      } else {
        showToast('SHORTCUT FAILED: ' + (d.error || 'unknown'));
        if (btn) { btn.textContent = '[ CREATE DESKTOP SHORTCUT ]'; btn.disabled = false; }
      }
    })
    .catch(() => {
      if (btn) { btn.textContent = '[ CREATE DESKTOP SHORTCUT ]'; btn.disabled = false; }
    });
}

// ── Widget Shape ──────────────────────────────────────────────
function setWidgetShape(shape) {
  ['orb','bar','chip'].forEach(s => {
    const id = 'wsShape' + s.charAt(0).toUpperCase() + s.slice(1);
    const btn = document.getElementById(id);
    if (btn) btn.classList.toggle('active', s === shape);
  });
  showToast('WIDGET SHAPE: ' + shape.toUpperCase() + ' (takes effect immediately)');
  fetch('/api/widget-shape', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ shape }),
  }).catch(() => {});
}

// ── Usage Stats ───────────────────────────────────────────────
function loadUsageStats() {
  fetch('/api/usage-stats')
    .then(r => r.json())
    .then(d => {
      const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
      set('usageTodayWords',   (d.today?.words   ?? 0).toLocaleString());
      set('usageTodayCount',   (d.today?.count   ?? 0) + ' sessions');
      set('usageTodayMins',    (d.today?.minutes  ?? 0));
      set('usageWeekWords',    (d.week?.words    ?? 0).toLocaleString());
      set('usageWeekCount',    (d.week?.count    ?? 0) + ' sessions');
      set('usageWeekMins',     (d.week?.minutes   ?? 0));
      set('usageSessionWords', (d.session?.words  ?? 0).toLocaleString());
      set('usageSessionCount', (d.session?.count  ?? 0) + ' sessions');
      set('usageSessionMins',  (d.session?.minutes ?? 0));

      // Language breakdown
      if (d.languages && d.languages.length > 1) {
        const box  = document.getElementById('usageLangBreakdown');
        const list = document.getElementById('usageLangList');
        if (box && list) {
          box.style.display = '';
          const total = d.languages.reduce((s, l) => s + l.cnt, 0);
          list.innerHTML = d.languages.map(l => {
            const pct = total > 0 ? Math.round(l.cnt / total * 100) : 0;
            const lang = l.language || 'auto';
            return `<span style="background:var(--vf-bg-1);border:1px solid var(--vf-b0);
              border-radius:4px;padding:3px 8px;font-size:10px;letter-spacing:1px;
              font-family:var(--vf-font-b)">${lang.toUpperCase()} ${pct}%</span>`;
          }).join('');
        }
      }
    })
    .catch(() => {});
}

// ── Share Modal ───────────────────────────────────────────────
function openShareModal() {
  const modal = document.getElementById('shareModal');
  if (modal) modal.style.display = 'flex';
}
function closeShareModal() {
  const modal = document.getElementById('shareModal');
  if (modal) modal.style.display = 'none';
}
function copyShareUrl() {
  const el = document.getElementById('shareUrl');
  if (el) { navigator.clipboard.writeText(el.value).then(() => showToast('LINK COPIED')); }
}
function copyShareText() {
  const el = document.getElementById('shareText');
  if (el) { navigator.clipboard.writeText(el.value).then(() => showToast('MESSAGE COPIED')); }
}
