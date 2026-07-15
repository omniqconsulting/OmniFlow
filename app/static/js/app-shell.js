// ── app-shell.js ─────────────────────────────────────────────────────────
// Loaded on every page (base.html), after the DOM as a normal <script src>.
//
// Section A below is the site's existing JS, moved here verbatim from the
// inline <script> blocks that used to live in base.html (July 2026
// Native-Feel PWA refactor) so it can be cached as a static asset by the
// service worker (see Section 6.2 of the Native-Feel UI Brief).
//
// Section B is the new native-feel layer: standalone-mode detection,
// install-prompt capture, and small reusable utilities (toast, vibrate).
//
// Note: the tiny inline theme-flash-prevention script in base.html's <head>
// intentionally stays inline — it must run synchronously before first paint
// to avoid a light/dark flash, which an external <script src> (deferred by
// default load timing) cannot guarantee.
// ────────────────────────────────────────────────────────────────────────

/* ══════════════════════════════════════════════════════════════════════
   SECTION A — existing site JS (moved from base.html, unchanged)
   ══════════════════════════════════════════════════════════════════════ */

// P6-02: service worker registration (app shell caching + web push)
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/static/sw.js').catch(function (err) {
      console.warn('SW registration failed', err);
    });
  });
}

// P6-03: auto-tag each <td> with its column header so table.mobile-cards can
// render a card layout at phone width (CSS in app-shell.css) without having
// to hand-edit every row's markup with data-label attributes.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('table.mobile-cards').forEach(function (t) {
    var headers = Array.from(t.querySelectorAll('thead th')).map(function (th) {
      var clone = th.cloneNode(true);
      clone.querySelectorAll('.sort-icon').forEach(function (el) { el.remove(); });
      return clone.textContent.trim();
    });
    t.querySelectorAll('tbody tr').forEach(function (tr) {
      Array.from(tr.children).forEach(function (td, i) {
        if (td.hasAttribute('colspan')) return;
        if (headers[i]) td.setAttribute('data-label', headers[i]);
      });
    });
  });
});

function urlBase64ToUint8Array(base64String) {
  var padding = '='.repeat((4 - base64String.length % 4) % 4);
  var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  var rawData = window.atob(base64);
  var outputArray = new Uint8Array(rawData.length);
  for (var i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
  return outputArray;
}

window.enablePushNotifications = async function (btn) {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    alert('Push notifications are not supported on this browser.');
    return;
  }
  try {
    var permission = await Notification.requestPermission();
    if (permission !== 'granted') { alert('Push permission was not granted.'); return; }
    var reg = await navigator.serviceWorker.ready;
    var keyResp = await fetch('/push/vapid-public-key');
    var keyData = await keyResp.json();
    var sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(keyData.publicKey),
    });
    var raw = sub.toJSON();
    var body = new URLSearchParams({
      endpoint: raw.endpoint,
      p256dh_key: raw.keys.p256dh,
      auth_key: raw.keys.auth,
    });
    await fetch('/push/subscribe', { method: 'POST', body: body });
    setPushButtonEnabled(btn);
  } catch (err) {
    console.warn('Push subscribe failed', err);
    alert('Could not enable push notifications on this device.');
  }
};

function setPushButtonEnabled(btn) {
  if (!btn) return;
  btn.textContent = '✓ Push notifications enabled';
  btn.disabled = true;
}

// P6-05: reflect the device's actual push subscription state on every page
// load, instead of always showing "Enable push notifications" regardless
// of whether this device already granted permission and subscribed.
document.addEventListener('DOMContentLoaded', async function () {
  var buttons = document.querySelectorAll('[data-push-btn]');
  if (!buttons.length) return;
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || typeof Notification === 'undefined') return;
  try {
    var reg = await navigator.serviceWorker.getRegistration();
    if (!reg) return;
    var sub = await reg.pushManager.getSubscription();
    if (Notification.permission === 'granted' && sub) {
      buttons.forEach(setPushButtonEnabled);
    }
  } catch (err) {
    console.warn('Push status check failed', err);
  }
});

// ── Multi-select widget (global) ─────────────────────────────────────────
(function(){
  function updateMsBtn(wrap) {
    var btn = wrap.querySelector('.ms-btn-text');
    var checked = wrap.querySelectorAll('.ms-item input:checked');
    var placeholder = wrap.dataset.placeholder || 'All';
    btn.textContent = checked.length ? checked.length + ' selected' : placeholder;
  }
  function openMs(wrap) {
    document.querySelectorAll('.ms-wrap.open').forEach(function(w){ if(w!==wrap) closeMs(w); });
    wrap.classList.add('open');
  }
  function closeMs(wrap){ wrap.classList.remove('open'); }

  document.addEventListener('click', function(e){
    if (!e.target.closest('.ms-wrap')) {
      document.querySelectorAll('.ms-wrap.open').forEach(closeMs);
    }
  });

  function syncMsHidden(wrap) {
    var name = wrap.dataset.name;
    var form = wrap.closest('form');
    if (!form) return;
    form.querySelectorAll('input[type=hidden][data-ms="'+name+'"]').forEach(function(h){ h.remove(); });
    wrap.querySelectorAll('.ms-item input:checked').forEach(function(c){
      var h = document.createElement('input');
      h.type = 'hidden'; h.name = name; h.value = c.value; h.dataset.ms = name;
      form.appendChild(h);
    });
  }

  window.toggleMs = function(btn) {
    var wrap = btn.closest('.ms-wrap');
    wrap.classList.contains('open') ? closeMs(wrap) : openMs(wrap);
  };

  window.onMsChange = function(chk) {
    var wrap = chk.closest('.ms-wrap');
    updateMsBtn(wrap);
    syncMsHidden(wrap);
  };

  window.msSelectAll = function(btn, mode) {
    var wrap = btn.closest('.ms-wrap');
    wrap.querySelectorAll('.ms-item input[type=checkbox]').forEach(function(c){
      c.checked = (mode === 'all');
    });
    updateMsBtn(wrap);
    syncMsHidden(wrap);
  };

  function ensureMsActions(wrap) {
    var panel = wrap.querySelector('.ms-panel');
    if (!panel || panel.querySelector('.ms-actions')) return;
    var row = document.createElement('div');
    row.className = 'ms-actions';
    row.innerHTML =
      '<button type="button" onclick="msSelectAll(this,\'all\')">Select all</button>' +
      '<button type="button" onclick="msSelectAll(this,\'none\')">None</button>';
    panel.insertBefore(row, panel.firstChild);
  }

  // On page load: restore button labels from pre-checked state & sync hidden inputs
  document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('.ms-wrap').forEach(function(wrap){
      ensureMsActions(wrap);
      updateMsBtn(wrap);
      // sync hidden inputs for any pre-checked boxes (page load from URL params)
      syncMsHidden(wrap);
    });
  });
})();

function togglePwVisibility(btn) {
  var inp = btn.previousElementSibling;
  if (inp.type === 'password') { inp.type = 'text'; btn.textContent = '🙈'; btn.setAttribute('aria-label', 'Hide password'); }
  else { inp.type = 'password'; btn.textContent = '👁'; btn.setAttribute('aria-label', 'Show password'); }
}

// ── WebSocket client + fallback polling + theme/sound toggles + help modal ──
// Guarded on body.is-authed (set server-side in base.html) since this logic
// only applies to logged-in pages — same scope the inline block used to have
// via Jinja's {% if user %}.
if (document.body.classList.contains('is-authed')) (function(){
  const wsDot  = document.getElementById('ws-dot');
  let ws       = null;
  let pollTimer = null;
  let lastPoll  = new Date().toISOString();

  // ── Badge helper ───────────────────────────────────────────────────────────
  // Updates every badge on the page — the desktop nav's #notif-badge and the
  // mobile top bar's badge both carry the .js-notif-badge class.
  function setBadge(n){
    document.querySelectorAll('.js-notif-badge').forEach(function(el){
      if(n > 0){ el.textContent = n > 99 ? '99+' : n; el.style.display='inline-block'; }
      else      { el.style.display='none'; }
    });
  }

  // ── P10-03: Web Audio API chime ──────────────────────────────────────────
  // Key fix: when the tab is backgrounded Chrome suspends the AudioContext.
  // ctx.resume() from a WebSocket handler (non-user-gesture) is rejected.
  // Solution: queue a pending chime and fire it on the next user interaction.
  let _audioCtx = null;
  let _pendingChime = false;

  function _getAudioCtx(){
    if(!_audioCtx){
      try{ _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch(e){ return null; }
    }
    return _audioCtx;
  }

  function _doPlayChime(ctx){
    try{
      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.type = 'sine';
      osc.frequency.setValueAtTime(880,  ctx.currentTime);
      osc.frequency.setValueAtTime(1108, ctx.currentTime + 0.1);
      gain.gain.setValueAtTime(0.18, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + 0.5);
    }catch(e){}
  }

  // Called on every click/keydown — resumes suspended context and plays
  // any queued chime (e.g. one that arrived while the tab was backgrounded).
  function _unlockAudio(){
    const ctx = _getAudioCtx(); if(!ctx) return;
    if(ctx.state === 'suspended'){
      ctx.resume().then(()=>{
        try{
          const buf = ctx.createBuffer(1,1,22050);
          const src = ctx.createBufferSource();
          src.buffer = buf; src.connect(ctx.destination); src.start(0);
        }catch(e){}
        if(_pendingChime){ _pendingChime = false; _doPlayChime(ctx); }
      }).catch(()=>{});
    } else if(_pendingChime){
      _pendingChime = false; _doPlayChime(ctx);
    }
  }
  document.addEventListener('click',   _unlockAudio, { once: false, capture: true });
  document.addEventListener('keydown', _unlockAudio, { once: false, capture: true });

  function playChime(){
    if(localStorage.getItem('sound_muted')==='1') return;
    try{
      const ctx = _getAudioCtx();
      if(!ctx) return;
      if(ctx.state !== 'running'){
        _pendingChime = true; // play on next user gesture
        return;
      }
      _doPlayChime(ctx);
    } catch(e){}
  }
  window._omniPlayChime = playChime; // exposed for the sound-mute toggle below

  // P10-03: Sound events (ticket assigned, help ticket, FMS stage, checklist overdue)
  const SOUND_EVENTS = new Set([
    'TICKET_ASSIGNED','TICKET_HELP_REQUESTED','FMS_STAGE_TRANSITION',
    'MATERIAL_REQUEST_UPDATE','CHECKLIST_OVERDUE',
  ]);

  // ── Toast helper (also exposed as window.showToast — see Section B) ──────
  const TOAST_COLOURS = {
    TICKET_ASSIGNED:'toast-blue', TICKET_STATUS_CHANGED:'',
    TICKET_FLAGGED:'toast-amber', TICKET_HELP_REQUESTED:'toast-red',
    CHECKLIST_OVERDUE:'toast-red', CHECKLIST_COMPLETED:'toast-green',
    NOTIFICATION_NEW:'', FMS_STAGE_TRANSITION:'', STORE_ALERT:'toast-amber',
  };
  function showToast(title, body, colour){
    const t = document.createElement('div');
    t.className = 'ws-toast ' + (colour||'');
    t.innerHTML = '<strong>' + escHtml(title) + '</strong>'
                + (body ? '<span>' + escHtml(body) + '</span>' : '');
    const existing = document.querySelectorAll('.ws-toast');
    const offset   = existing.length * 68;
    t.style.bottom = (24 + offset) + 'px';
    document.body.appendChild(t);
    requestAnimationFrame(()=>{ t.classList.add('show'); });
    setTimeout(()=>{
      t.classList.remove('show');
      setTimeout(()=>t.remove(), 300);
    }, 4500);
  }
  window.showToast = showToast;
  function escHtml(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

  // ── Event dispatcher (handles events from both WS and poll) ──────────────
  function handleEvent(event, data){
    if(data.unread_count !== undefined) setBadge(data.unread_count);
    if(event === 'CONNECTED' || event === 'PING') return;
    const title = data.title || data.ticket_title || event.replace(/_/g,' ');
    const body  = data.body  || data.message || '';
    const colour = TOAST_COLOURS[event] || '';
    showToast(title, body, colour);
    if(SOUND_EVENTS.has(event)) playChime();
  }

  // ── Polling fallback (1-5) ────────────────────────────────────────────────
  function startPolling(){
    if(pollTimer) return;
    pollTimer = setInterval(async ()=>{
      try{
        const r   = await fetch('/api/poll?since=' + encodeURIComponent(lastPoll));
        const obj = await r.json();
        lastPoll  = obj.ts || lastPoll;
        setBadge(obj.unread_count || 0);
        (obj.events||[]).forEach(e => handleEvent(e.event, e.data));
      } catch(e){}
    }, 30000);
  }
  function stopPolling(){ if(pollTimer){ clearInterval(pollTimer); pollTimer=null; } }

  // ── WebSocket client (1-1 / 1-2 / 1-3) ───────────────────────────────────
  let reconnectDelay = 2000;
  function connect(){
    if(!('WebSocket' in window)){ startPolling(); return; }
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    try{ ws = new WebSocket(proto + '//' + location.host + '/ws'); }
    catch(e){ startPolling(); return; }

    ws.onopen = ()=>{
      if(wsDot){ wsDot.classList.add('connected'); wsDot.title='Real-time sync: connected'; }
      stopPolling();
      reconnectDelay = 2000;
      setInterval(()=>{ if(ws && ws.readyState===1) ws.send('ping'); }, 30000);
    };

    ws.onmessage = (e)=>{
      try{
        const msg = JSON.parse(e.data);
        if(msg === 'pong') return;
        if(msg.event === 'PING'){ if(ws && ws.readyState===1) ws.send('ping'); return; }
        handleEvent(msg.event, msg.data || {});
      } catch(ex){}
    };

    ws.onclose = (ev)=>{
      if(wsDot){ wsDot.classList.remove('connected'); wsDot.title='Real-time sync: reconnecting…'; }
      if(ev.code === 4001){ startPolling(); return; }
      startPolling();
      setTimeout(()=>{ reconnectDelay = Math.min(reconnectDelay*1.5, 30000); connect(); }, reconnectDelay);
    };

    ws.onerror = ()=>{ try{ ws.close(); } catch(e){} };
  }

  connect();

  // ── Theme toggle ──────────────────────────────────────────────────────────
  const themeBtn = document.getElementById('theme-btn');
  function applyTheme(){
    const isLight = document.documentElement.classList.contains('light');
    if(themeBtn) themeBtn.textContent = isLight ? '☀' : '🌙';
    if(themeBtn) themeBtn.title = isLight ? 'Switch to dark theme' : 'Switch to light theme';
  }
  applyTheme();
  window.toggleTheme = function(){
    const isLight = document.documentElement.classList.toggle('light');
    localStorage.setItem('theme', isLight ? 'light' : 'dark');
    applyTheme();
  };

  // ── Sound mute toggle ────────────────────────────────────────────────────
  // Updates every mute button on the page — the desktop nav's #sound-btn and
  // the mobile top bar's mute icon both carry the .js-sound-btn class.
  function applyMute(){
    const muted = localStorage.getItem('sound_muted') === '1';
    document.querySelectorAll('.js-sound-btn').forEach(function(btn){
      // PWA-only: the mobile top bar's mute button (.mt-sound-btn) uses a
      // speaker glyph so it's visually distinct from the adjacent
      // notifications bell — desktop's #sound-btn keeps its original bell
      // glyph unchanged.
      const isMobileTopbar = btn.classList.contains('mt-sound-btn');
      btn.textContent = isMobileTopbar ? (muted ? '🔇' : '🔊') : (muted ? '🔕' : '🔔');
      btn.title = muted ? 'Sounds muted — click to enable' : 'Notification sounds on — click to mute';
    });
  }
  applyMute();
  window.toggleSound = function(){
    const muted = localStorage.getItem('sound_muted') === '1';
    localStorage.setItem('sound_muted', muted ? '0' : '1');
    applyMute();
    if(muted) setTimeout(playChime, 80); // play test chime on unmute to unlock context
  };

  // ── "Need Help" modal ────────────────────────────────────────────────────
  async function _loadHelpAssignees(){
    const sel = document.getElementById('help-assignee');
    try{
      const r = await fetch('/api/team-members');
      const members = await r.json();
      sel.innerHTML = '<option value="">— Select a person —</option>';
      members.forEach(m => {
        const o = document.createElement('option');
        o.value = m.id;
        o.textContent = m.name + ' (' + m.role + ')';
        sel.appendChild(o);
      });
    }catch(e){
      sel.innerHTML = '<option value="">— Select a person —</option>';
    }
  }
  window.openHelpModal = function(){
    document.getElementById('help-title').value = '';
    document.getElementById('help-desc').value = '';
    document.getElementById('help-form-body').style.display = '';
    document.getElementById('help-success').style.display = 'none';
    document.getElementById('help-submit-btn').disabled = false;
    document.getElementById('help-submit-btn').textContent = 'Send Help Request →';
    document.getElementById('help-modal-bg').classList.add('open');
    _loadHelpAssignees();
    setTimeout(()=>document.getElementById('help-title').focus(), 80);
  };
  window.closeHelpModal = function(){
    document.getElementById('help-modal-bg').classList.remove('open');
  };
  window.submitHelpRequest = async function(){
    const assignee = document.getElementById('help-assignee').value;
    if(!assignee){ document.getElementById('help-assignee').focus(); alert('Please select who you need help from.'); return; }
    const title = document.getElementById('help-title').value.trim();
    if(!title){ document.getElementById('help-title').focus(); return; }
    const btn = document.getElementById('help-submit-btn');
    btn.disabled = true; btn.textContent = 'Sending…';
    try{
      const fd = new FormData();
      fd.append('title', title);
      fd.append('description', document.getElementById('help-desc').value.trim());
      fd.append('assignee_id', document.getElementById('help-assignee').value);
      const r = await fetch('/help-request', {method:'POST', body: fd});
      if(r.ok){
        const data = await r.json();
        document.getElementById('help-form-body').style.display = 'none';
        const who = data.assignee ? ' — notified ' + data.assignee + '.' : '.';
        document.getElementById('help-success-sub').textContent =
          'Ticket ' + (data.display_id || '') + ' created' + who;
        document.getElementById('help-success').style.display = 'block';
      } else {
        btn.disabled = false; btn.textContent = 'Send Help Request →';
        alert('Something went wrong. Please try again.');
      }
    }catch(e){
      btn.disabled = false; btn.textContent = 'Send Help Request →';
      alert('Network error. Please try again.');
    }
  };
  document.addEventListener('keydown', function(e){
    if(e.key==='Escape') closeHelpModal();
  });
})();

/* ══════════════════════════════════════════════════════════════════════
   SECTION B — native-feel layer (Native-Feel UI Brief, Section 5)
   ══════════════════════════════════════════════════════════════════════ */

// 5.4 — Standalone detection: add an `is-standalone` class to <html> so any
// JS-driven behaviour (haptics, install prompt, future skeleton loaders) can
// branch on it. Falls back silently to "not standalone" everywhere this
// isn't supported (older Android WebViews, desktop browsers).
(function(){
  const isStandalone =
    (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
    window.navigator.standalone === true; // iOS Safari
  if(isStandalone) document.documentElement.classList.add('is-standalone');

  // Tickets Redesign (2026-07): mirror standalone-ness into a `pwa_ui` cookie
  // so server routes can pick the PWA-only templates without any UA sniffing.
  // Only flips (and reloads) when the signal actually changes, so this never
  // loops and never affects a normal browser tab (isStandalone stays false there).
  const hasPwaCookie = document.cookie.split('; ').some(c => c === 'pwa_ui=1');
  if(isStandalone && !hasPwaCookie){
    document.cookie = 'pwa_ui=1;path=/;max-age=31536000';
    window.location.reload();
  } else if(!isStandalone && hasPwaCookie){
    document.cookie = 'pwa_ui=;path=/;max-age=0';
    window.location.reload();
  }
})();

// 5.1 — Bottom-nav active state: the partial already renders `.active` from
// the server (same request.url.path check the desktop nav uses), this just
// keeps a body-level class in sync for any CSS that needs to know a bottom
// nav is present (see .has-bottom-nav padding rules in app-shell.css).
document.addEventListener('DOMContentLoaded', function(){
  if(document.querySelector('.bottom-nav')) document.body.classList.add('has-bottom-nav');
});

// Checklists Redesign (2026-07): make the "Need Help" SOS button a draggable
// floating button on mobile/PWA layouts only (desktop keeps the static
// bottom-right pill — see .help-fab in app-shell.css, unaffected here). This
// lets the user park it wherever it doesn't collide with a module's own FAB
// (Tickets/Delegation "+", Checklists "+", etc.) instead of every module
// having to route around a fixed SOS position. Position persists across
// visits via localStorage; a tap still opens the help modal — only a real
// drag (past a small movement threshold) suppresses the click.
(function(){
  const DRAG_THRESHOLD = 6; // px — anything less is treated as a tap
  const POS_KEY = 'help_fab_pos';

  function isMobileLayout(){
    return document.body.classList.contains('has-bottom-nav');
  }

  function clamp(val, min, max){ return Math.min(Math.max(val, min), max); }

  function applyStoredPosition(fab){
    let saved;
    try { saved = JSON.parse(localStorage.getItem(POS_KEY)); } catch(e) { saved = null; }
    if(!saved) return;
    const w = fab.offsetWidth || 46, h = fab.offsetHeight || 46;
    const left = clamp(saved.left, 4, window.innerWidth - w - 4);
    const top = clamp(saved.top, 4, window.innerHeight - h - 4);
    fab.style.left = left + 'px';
    fab.style.top = top + 'px';
    fab.style.right = 'auto';
    fab.style.bottom = 'auto';
  }

  function initDraggableFab(){
    const fab = document.querySelector('.help-fab');
    if(!fab || !isMobileLayout()) return;
    fab.classList.add('help-fab-draggable');
    applyStoredPosition(fab);

    // Perf note: the previous version read fab.offsetWidth/offsetHeight (a
    // layout read) on every single pointermove, right after a style write —
    // that forces a synchronous reflow per event and is what made the drag
    // feel janky. Fix: cache the fab's size once per drag (it can't change
    // mid-drag), and move it via `transform` (compositor-only, no layout/
    // paint) instead of writing left/top on every event; left/top are only
    // committed once, on pointerup. Frames are also coalesced through rAF so
    // a burst of touch events collapses into at most one paint per frame.
    let dragging = false, moved = false;
    let startX = 0, startY = 0, startLeft = 0, startTop = 0, fabW = 0, fabH = 0;
    let pendingDx = 0, pendingDy = 0, rafId = null;

    function applyFrame(){
      rafId = null;
      if(!dragging) return;
      const left = clamp(startLeft + pendingDx, 4, window.innerWidth - fabW - 4);
      const top = clamp(startTop + pendingDy, 4, window.innerHeight - fabH - 4);
      fab.style.transform = 'translate3d(' + (left - startLeft) + 'px,' + (top - startTop) + 'px,0)';
    }

    function pointerDown(e){
      const rect = fab.getBoundingClientRect();
      dragging = true; moved = false;
      startX = e.clientX; startY = e.clientY;
      startLeft = rect.left; startTop = rect.top;
      fabW = fab.offsetWidth; fabH = fab.offsetHeight; // read once, before any writes
      fab.setPointerCapture(e.pointerId);
    }
    function pointerMove(e){
      if(!dragging) return;
      const dx = e.clientX - startX, dy = e.clientY - startY;
      if(!moved && Math.hypot(dx, dy) > DRAG_THRESHOLD){
        moved = true;
        fab.classList.add('help-fab-dragging');
      }
      if(!moved) return;
      pendingDx = dx; pendingDy = dy;
      if(rafId === null) rafId = requestAnimationFrame(applyFrame);
    }
    function pointerUp(e){
      if(!dragging) return;
      dragging = false;
      if(rafId !== null){ cancelAnimationFrame(rafId); rafId = null; }
      fab.classList.remove('help-fab-dragging');
      if(moved){
        const left = clamp(startLeft + pendingDx, 4, window.innerWidth - fabW - 4);
        const top = clamp(startTop + pendingDy, 4, window.innerHeight - fabH - 4);
        // Commit the transform into a real left/top so it survives reflows
        // (e.g. keyboard open/close, orientation change) the same way the
        // stored position does, then drop the transform.
        fab.style.transform = '';
        fab.style.left = left + 'px';
        fab.style.top = top + 'px';
        fab.style.right = 'auto';
        fab.style.bottom = 'auto';
        try {
          localStorage.setItem(POS_KEY, JSON.stringify({ left, top }));
        } catch(err) {}
      }
    }
    // Suppress the click that follows a real drag, so openHelpModal() only
    // fires on an actual tap.
    fab.addEventListener('click', function(e){
      if(moved){ e.preventDefault(); e.stopImmediatePropagation(); moved = false; }
    }, true);
    fab.addEventListener('pointerdown', pointerDown);
    fab.addEventListener('pointermove', pointerMove);
    fab.addEventListener('pointerup', pointerUp);
    fab.addEventListener('pointercancel', pointerUp);

    // Keep it on-screen across viewport/orientation changes.
    window.addEventListener('resize', function(){ applyStoredPosition(fab); });
  }

  document.addEventListener('DOMContentLoaded', initDraggableFab);
})();

// 5.4 — Install prompt capture: stash the deferred prompt so a future
// "Install OmniFlow" button (not part of this brief) can trigger it on
// demand instead of relying on the browser's own mini-infobar timing.
window.addEventListener('beforeinstallprompt', function(e){
  e.preventDefault();
  window._omniDeferredInstallPrompt = e;
});

// 5.5 — Vibration helper: no-ops silently on unsupported browsers (iOS).
window.omniVibrate = function(pattern){
  try{ if(navigator.vibrate) navigator.vibrate(pattern || 15); }catch(e){}
};

// Shared modal helpers (components/modal.html and any hand-rolled modal using
// the same display:none/flex + backdrop pattern from setup/customers.html).
window.openModal = function(modalId){
  const el = document.getElementById(modalId);
  if(!el) return;
  el.style.display = 'flex';
  const panel = el.querySelector('.modal-box');
  if(panel) panel.focus();
};
window.closeModal = function(modalId){
  const el = document.getElementById(modalId);
  if(!el) return;
  el.style.display = 'none';
};
document.addEventListener('keydown', function(e){
  if(e.key !== 'Escape') return;
  document.querySelectorAll('.omni-modal-backdrop').forEach(function(el){
    if(el.style.display === 'flex') el.style.display = 'none';
  });
});

// Mobile top bar's account menu (base.html): tap the account icon to open,
// backdrop/Escape to close.
window.toggleAccountMenu = function(force){
  const bd = document.getElementById('account-menu-backdrop');
  if(!bd) return;
  const willOpen = typeof force === 'boolean' ? force : !bd.classList.contains('open');
  bd.classList.toggle('open', willOpen);
};
document.addEventListener('DOMContentLoaded', function(){
  const bd = document.getElementById('account-menu-backdrop');
  if(bd) bd.addEventListener('click', function(e){ if(e.target === bd) toggleAccountMenu(false); });
});
document.addEventListener('keydown', function(e){
  if(e.key === 'Escape') toggleAccountMenu(false);
});

// Bottom nav horizontal scroll (bottom_nav.html): each full page load starts
// scrolled to the left, so on a page whose tab lives further right (e.g.
// Inventory, Setup) we scroll it into view instead of leaving the user to
// find it again. We also toggle a soft edge fade + nudge on whichever side
// still has more content, so it's clear the row scrolls.
document.addEventListener('DOMContentLoaded', function(){
  const inner = document.getElementById('bottom-nav-inner');
  if(!inner) return;
  const active = inner.querySelector('a.active');
  if(active) active.scrollIntoView({ inline: 'center', block: 'nearest' });

  const fadeL = document.querySelector('.bottom-nav-fade-l');
  const fadeR = document.querySelector('.bottom-nav-fade-r');
  function updateFades(){
    if(fadeL) fadeL.classList.toggle('show', inner.scrollLeft > 4);
    if(fadeR) fadeR.classList.toggle('show', inner.scrollLeft < inner.scrollWidth - inner.clientWidth - 4);
  }
  updateFades();
  inner.addEventListener('scroll', updateFades, { passive: true });
  window.addEventListener('resize', updateFades);
});
