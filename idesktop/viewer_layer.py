"""Our layer on top of pymobiledevice3's serve-web viewer page.

Re-read on every page load (see native.build_html), so editing this file and pressing F5 in the
viewer updates the UI without restarting the stream -- which matters during calls, when iOS
refuses to start a new HD session.
"""

HEAD_INJECT = """
<script>
// First run: start with both side panels collapsed so the phone gets the window.
try { for (const s of ['left','right']) if (!localStorage.getItem('tray-'+s)) localStorage.setItem('tray-'+s,'collapsed'); } catch (e) {}
</script>
<style>
 #brand, #brand-logo { display:none }
 #topbar { min-height:0; padding-top:4px; padding-bottom:4px }
 #stage-wrap { padding:4px 8px; gap:8px }
 #bottom-row { flex-wrap:wrap; justify-content:center; gap:4px }
 #bottom-row .btn { padding:4px 10px; font-size:12px }
 @media (max-width: 900px) { body:not(.ext-more) #left-tray, body:not(.ext-more) #right-tray { display:none } }
 canvas { image-rendering:auto !important }
 /* Big Screen: fullscreen, no chrome, toolbar slides up when the mouse nears the bottom edge. */
 body.ext-big #topbar, body.ext-big #left-tray, body.ext-big #right-tray { display:none !important }
 body.ext-big { overflow:hidden; background:#000 }
 body.ext-big #stage-wrap { padding:0; gap:0; justify-content:center; height:100vh }
 body.ext-big #stage::before { display:none }
 body.ext-big #bottom-row { position:fixed; left:50%; bottom:10px; transform:translate(-50%, 140%);
   transition:transform .18s ease; z-index:50; background:#111d; backdrop-filter:blur(8px);
   border-radius:12px; padding:6px; max-width:96vw }
 body.ext-big.ext-show-row #bottom-row { transform:translate(-50%, 0) }
 body.ext-big canvas { border-radius:0 !important }
 #ext-toast { position:fixed; left:50%; bottom:14px; transform:translateX(-50%); background:#000c; color:#fff;
              font:12px system-ui; padding:6px 12px; border-radius:8px; pointer-events:none; opacity:0;
              transition:opacity .25s }
 #ext-toast.show { opacity:1 }
 /* Only show "Stream offline" when it lasts: brief gaps (window brought back from the background,
    a dropped keyframe, a still screen) recover by themselves within a second or two. */
 #offline-overlay:not(.hidden) { animation:ext-late 3s steps(1, end) both }
 body.ext-basic-on #offline-overlay { display:none !important }
 @keyframes ext-late { from { opacity:0; visibility:hidden } to { opacity:1; visibility:visible } }
 /* Floating iPhone: the helper clips the browser window to the phone body, its side buttons and
    the floating controls (winshape.py), so everything else is see-through desktop. */
 html:has(body.ext-compact), body.ext-compact { background:#000 !important; overflow:hidden; margin:0 }
 body.ext-compact #topbar, body.ext-compact #workspace { display:none !important }
 #ext-phone, #ext-ctl { display:none }
 body.ext-compact #ext-phone { display:block; position:fixed; top:0; box-sizing:border-box; touch-action:none;
   background:linear-gradient(140deg,#a4a19a 0%,#55534f 22%,#8d8a84 48%,#3e3c39 74%,#9c9993 100%); cursor:grab; z-index:10 }
 body.ext-compact #ext-phone:active { cursor:grabbing }
 #ext-bezel { position:absolute; box-sizing:border-box; background:#030303;
   box-shadow:inset 0 0 0 1px #1d1d1f, inset 0 0 6px #000 }
 body.ext-compact #ext-bezel canvas { display:block; position:absolute }
 body.ext-compact #ext-phone .hw { position:absolute !important; display:block !important; margin:0 !important;
   padding:0 !important; border:0 !important; min-width:0 !important; transform:none !important;
   background:linear-gradient(90deg,#57554f,#a19e97 55%,#4f4d49) !important; border-radius:2px !important;
   box-shadow:none !important; cursor:pointer !important; opacity:1 !important }
 body.ext-compact #ext-phone .hw:hover { filter:brightness(1.25) }
 body.ext-compact #ext-phone .hw:active { filter:brightness(.8) }
 #ext-camctl { position:absolute; border-radius:2px; background:linear-gradient(90deg,#4f4d49,#8f8c86 50%,#4f4d49) }
 body.ext-compact #ext-ctl { display:block; position:fixed; top:0; z-index:20; font:500 13px/1 -apple-system,"Segoe UI Variable Text","Segoe UI",system-ui,sans-serif }
 .ext-fab, .ext-pill { position:absolute; box-sizing:border-box; display:flex; align-items:center; gap:10px; cursor:pointer;
   color:#f2f2f7; background:linear-gradient(180deg,#2c2c31,#1c1c20); border:1px solid #3a3a40;
   box-shadow:inset 0 1px 0 #ffffff14; user-select:none; transition:background .12s, border-color .12s }
 .ext-fab:hover, .ext-pill:hover { background:linear-gradient(180deg,#3a3a41,#26262b); border-color:#4c4c54 }
 .ext-fab:active, .ext-pill:active { background:#18181b }
 .ext-fab { justify-content:center; border-radius:50% }
 .ext-pill { padding:0 14px 0 12px; border-radius:999px; white-space:nowrap }
 .ext-pill.on { border-color:#0a84ff; color:#fff }
 .ext-pill.needs-auto { opacity:.45 }
 .ext-pill.danger:hover { background:linear-gradient(180deg,#5a2224,#3d1618); border-color:#8a2c30 }
 .ext-fab svg, .ext-pill svg { width:16px; height:16px; flex:none; stroke:currentColor; fill:none; stroke-width:1.9;
   stroke-linecap:round; stroke-linejoin:round; opacity:.92 }
 .ext-pill:not(.show) { display:none }
 body.ext-compact #ext-to-compact, body.ext-compact #ext-more-btn { display:none }
</style>
"""

BODY_INJECT = """
<div id="ext-toast"></div>
<script>
(() => {
  const EXT = 'http://127.0.0.1:__EXT_PORT__';
  const report = (msg) => fetch(EXT + '/log', {method: 'POST', body: JSON.stringify({msg})}).catch(() => {});
  window.addEventListener('error', (e) => report((e.message || '') + ' @' + (e.lineno || '?') + ' ' + (e.error && e.error.stack || '')));
  window.addEventListener('unhandledrejection', (e) => report('rejection: ' + (e.reason && (e.reason.stack || e.reason))));
  const canvas = document.getElementById('c');
  const row = document.getElementById('bottom-row');
  const toastEl = document.getElementById('ext-toast');
  let toastT = 0;
  const toast = (m) => { toastEl.textContent = m; toastEl.classList.add('show');
                         clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove('show'), 1600); };
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  // In-order touch delivery. Upstream fires one fetch per pointer event without waiting, so
  // they race across the browser's few connections (video, audio and clipboard streams hold
  // some open) and a "release" can land before the moves -> iOS sees a tap instead of a drag.
  // Send strictly in order on one chain; while a request is in flight, collapse queued moves
  // to the newest one (contact-start and release are never dropped).
  const rawPost = (path, payload) => fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                  body: JSON.stringify(payload)});
  let tq = [], tBusy = false;
  async function pumpTouch() {
    if (tBusy) return; tBusy = true;
    try {
      while (tq.length) {
        const item = tq.shift();
        try { await rawPost('/touch', item); } catch (e) {}
      }
    } finally { tBusy = false; }
  }
  let touchDown = false;
  function sendTouch(p) {
    const isMove = p.type === 'contact' && touchDown;
    if (p.type === 'contact') touchDown = true;
    if (p.type === 'release' || p.type === 'tap') touchDown = false;
    const last = tq[tq.length - 1];
    if (isMove && last && last._move) tq[tq.length - 1] = Object.assign(p, {_move: true});
    else tq.push(isMove ? Object.assign(p, {_move: true}) : p);
    pumpTouch();
    return new Promise(r => { const t = () => (tq.length || tBusy) ? setTimeout(t, 4) : r(); t(); });
  }
  const origPost = window.postJson;
  window.postJson = function (path, payload) {
    if (path === '/touch') return sendTouch(Object.assign({}, payload));
    return origPost(path, payload);
  };
  const touch = (type, x, y) => sendTouch({type, x: Math.round(x), y: Math.round(y)});
  // Drag in HID space (0..65535) with n samples over ms, optional hold before lifting.
  async function drag(x0, y0, x1, y1, ms = 220, hold = 0, n = 12) {
    await touch('contact', x0, y0);
    for (let i = 1; i <= n; i++) { await sleep(ms / n); await touch('contact', x0 + (x1-x0)*i/n, y0 + (y1-y0)*i/n); }
    if (hold) await sleep(hold);
    await touch('release', x1, y1);
  }
  const gestures = {
    'App Switcher':   () => drag(32768, 65400, 32768, 40000, 320, 650),
    'Control Center': () => drag(60000, 250, 60000, 34000, 260),
    'Notifications':  () => drag(16000, 250, 16000, 40000, 260),
    'Spotlight':      () => drag(32768, 22000, 32768, 40000, 220),
    'Back':           () => drag(300, 32768, 46000, 32768, 220),
  };
  // Add our buttons next to the built-in Swipe / Home / Siri row.
  for (const [name, fn] of Object.entries(gestures)) {
    const b = document.createElement('button'); b.className = 'btn'; b.textContent = name;
    b.addEventListener('click', () => fn()); row.appendChild(b);
  }
  for (const [label, s] of [['Zoom +', 1.8], ['Zoom −', 0.5]]) {
    const b = document.createElement('button'); b.className = 'btn'; b.textContent = label;
    b.title = 'Two-finger pinch at screen centre (or Ctrl + mouse wheel over the screen)';
    b.addEventListener('click', () => pinch(0.5, 0.5, s)); row.appendChild(b);
  }

  { const b = document.createElement('button'); b.className = 'btn'; b.textContent = 'More'; b.id = 'ext-more-btn';
    b.title = 'Show the full panels (rotate, screenshot, sound, clipboard, accessibility)';
    b.addEventListener('click', () => { document.body.classList.toggle('ext-more'); window.fitCanvasToViewport(); });
    row.appendChild(b); }

  // Big Screen mode (button / Ctrl+B; Esc or the button again to leave).
  let hadFrame = null;
  const bigBtn = document.createElement('button'); bigBtn.className = 'btn'; bigBtn.textContent = 'Big Screen';
  bigBtn.title = 'Fill the whole monitor (Ctrl+B). Esc to exit.';
  row.appendChild(bigBtn);
  let bigFloat = false;   // Big Screen inside the floating phone's (already screen-sized) window
  function setBig(on) {
    const body = document.body;
    if (stored('ext-compact') !== '0') {
      // Floating phone: browser fullscreen would knock Edge out of the native-frame mode the
      // window clip relies on, so grow the phone inside the maximized window instead.
      if (on === bigFloat) return;
      bigFloat = on;
      bigBtn.textContent = on ? 'Exit Big Screen' : 'Big Screen';
      if (on) toast('Big Screen - Esc to exit');
      setTimeout(() => { window.fitCanvasToViewport(); if (lastFrame) try { window.drawFrame(lastFrame); } catch (e) {} }, 0);
      return;
    }
    if (on === body.classList.contains('ext-big')) return;
    if (on) {
      hadFrame = body.classList.contains('frame-on');
      body.classList.remove('frame-on');
      body.classList.add('ext-big');
      document.documentElement.requestFullscreen?.().catch(() => {});
      toast('Big Screen - move the mouse to the bottom edge for buttons, Esc to exit');
    } else {
      body.classList.remove('ext-big', 'ext-show-row');
      if (hadFrame) body.classList.add('frame-on');
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    }
    bigBtn.textContent = on ? 'Exit Big Screen' : 'Big Screen';
    try { localStorage.setItem('ext-big', on ? '1' : '0'); } catch (e) {}
    setTimeout(() => { window.fitCanvasToViewport(); if (lastFrame) try { window.drawFrame(lastFrame); } catch (e) {} }, 60);
  }
  bigBtn.addEventListener('click', () => setBig(!document.body.classList.contains('ext-big')));
  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey && !e.altKey && e.key.toLowerCase() === 'b') {
      e.preventDefault(); e.stopImmediatePropagation();
      setBig(!document.body.classList.contains('ext-big'));
    }
  }, true);
  // Desktop Mode: the closest an iPhone gets to an iPad on a monitor. Rotates the phone to
  // landscape (apps that support it fill the screen's width), drops Dynamic Type to extra-small
  // (much denser layouts), and goes Big Screen. Exiting restores the user's own text size.
  const deskBtn = document.createElement('button'); deskBtn.className = 'btn'; deskBtn.textContent = 'Desktop Mode';
  deskBtn.title = 'Landscape + extra-small text + full screen (Ctrl+D). Click again to restore.';
  row.appendChild(deskBtn);
  const store = (k, v) => { try { v === undefined ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} };
  const stored = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  async function setAx(key, value) {
    try {
      const r = await fetch('/accessibility/set', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                   body: JSON.stringify({key, value})});
      return r.ok;
    } catch (e) { return false; }
  }
  let deskOn = stored('ext-desk') === '1';
  deskBtn.textContent = deskOn ? 'Exit Desktop Mode' : 'Desktop Mode';
  async function setDesktop(on) {
    if (on === deskOn) return;
    deskOn = on;
    deskBtn.textContent = on ? 'Exit Desktop Mode' : 'Desktop Mode';
    // Classic window: also go Big Screen (first, while we still hold the click's user gesture -
    // fullscreen needs it). The floating phone is already as big as the screen: text only.
    const floating = stored('ext-compact') !== '0';
    if (!floating) setBig(on);
    if (on) {
      store('ext-desk', '1');
      if (!stored('ext-desk-text')) {
        try {
          const ax = await (await fetch('/accessibility')).json();
          const ts = (ax.settings || []).find(x => x.key === 'text_size');
          // Already extra-small (e.g. left over)? Then "your size" is the iOS default, not that.
          store('ext-desk-text', ts && ts.value !== 'extraSmall' ? ts.value : 'large');
        } catch (e) { store('ext-desk-text', 'large'); }
      }
      await setAx('text_size', 'extraSmall');
      toast(floating ? 'Desktop Mode: extra-small text for denser, iPad-like layouts.'
                     : 'Desktop Mode: extra-small text, full screen. Turn the phone sideways in an app that rotates to fill the width.');
    } else {
      await setAx('text_size', stored('ext-desk-text') || 'large');
      store('ext-desk'); store('ext-desk-text');
      toast('Desktop Mode off - text size restored');
    }
  }
  deskBtn.addEventListener('click', () => setDesktop(!deskOn));
  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey && !e.altKey && e.key.toLowerCase() === 'd') {
      e.preventDefault(); e.stopImmediatePropagation(); setDesktop(!deskOn);
    }
  }, true);

  // Leaving fullscreen with Esc (handled by the browser) also leaves Big Screen.
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !bigFloat) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (deskOn) setDesktop(false); else setBig(false);
  }, true);
  document.addEventListener('fullscreenchange', () => {
    if (document.fullscreenElement) return;
    if (deskOn) setDesktop(false); else setBig(false);   // Esc leaves Desktop Mode entirely
  });
  window.addEventListener('mousemove', (e) => {
    if (!document.body.classList.contains('ext-big')) return;
    document.body.classList.toggle('ext-show-row', e.clientY > window.innerHeight - 90);
  });

  // Pinch goes through WebDriverAgent (native touch is single-finger). Each one takes ~1-3 s in
  // XCTest, so zoom input arriving meanwhile is merged into one queued pinch, not dropped.
  let pinchBusy = false, pinchNext = null;
  async function pinch(nx, ny, scale) {
    if (pinchBusy) {
      const s = (pinchNext ? pinchNext.scale : 1) * scale;
      pinchNext = {nx, ny, scale: Math.max(0.3, Math.min(3.5, s))};
      return;
    }
    pinchBusy = true;
    try {
      const r = await fetch(EXT + '/pinch', {method:'POST', body: JSON.stringify({x: nx, y: ny, scale})});
      if (!r.ok) toast('Pinch unavailable: ' + (await r.text()));
    } catch (e) { toast('Pinch unavailable (helper not running)'); }
    finally {
      pinchBusy = false;
      const n = pinchNext; pinchNext = null;
      if (n && Math.abs(Math.log(n.scale)) > 0.05) pinch(n.nx, n.ny, n.scale);
    }
  }

  // Mouse wheel = scroll (drag), Ctrl + wheel = pinch zoom, Shift + wheel = sideways.
  let zoomAcc = 0, zoomT = 0;
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const p = touchCoords(e);   // viewer.js global: rotation-aware client -> HID 0..65535
    if (e.ctrlKey) {
      zoomAcc += e.deltaY; clearTimeout(zoomT);
      zoomT = setTimeout(() => { const s = zoomAcc < 0 ? 1.8 : 0.55; zoomAcc = 0; pinch(p.x/65535, p.y/65535, s); }, 120);
      return;
    }
    wheelScroll(e, p);
  }, {passive: false});

  // Wheel scrolling as one continuous finger, like a trackpad: touch down on the first tick, move
  // with every tick, lift shortly after the wheel stops (iOS adds its own momentum on the lift).
  // One gesture at a time - separate overlapping swipes per burst of ticks made the finger jump.
  // WHEEL_GAIN: finger travel (HID units, 0..65535 = full screen) per wheel pixel; one notch is ~100 px.
  // The finger rests WHEEL_REST_MS before lifting, which keeps iOS from adding much momentum.
  // localStorage 'ext-wheel' scales the speed (e.g. 1.5 = faster).
  const WHEEL_GAIN = 22 * (parseFloat(stored('ext-wheel')) || 1), WHEEL_IDLE_MS = 90, WHEEL_REST_MS = 70;
  const LO = 4000, HI = 61500;
  let wheelDown = false, wheelResting = false, wx = 0, wy = 0, wheelIdle = 0;
  function wheelScroll(e, p) {
    if (activePointer !== null) return;          // a real mouse drag is in progress
    const unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1;   // lines / pages -> px
    let dx = (e.shiftKey ? e.deltaY : e.deltaX) * unit, dy = (e.shiftKey ? 0 : e.deltaY) * unit;
    dx = -dx * WHEEL_GAIN; dy = -dy * WHEEL_GAIN;
    if (!wheelDown && wheelResting) { wheelResting = false; wheelDown = true; }   // resumed mid-rest: same finger
    if (!wheelDown) {
      // Start where the mouse is, but leave room to travel in the scroll direction.
      wx = Math.max(LO, Math.min(HI, p.x)); wy = Math.max(LO, Math.min(HI, p.y));
      if (dy > 0) wy = Math.min(wy, 26000); else if (dy < 0) wy = Math.max(wy, 39500);
      if (dx > 0) wx = Math.min(wx, 26000); else if (dx < 0) wx = Math.max(wx, 39500);
      wheelDown = true;
      touch('contact', wx, wy);
    }
    let nx = wx + dx, ny = wy + dy;
    if (nx < LO || nx > HI || ny < LO || ny > HI) {
      // Ran out of screen: lift here and carry on from the other side (like re-placing a finger).
      nx = Math.max(LO, Math.min(HI, nx)); ny = Math.max(LO, Math.min(HI, ny));
      touch('contact', nx, ny);
      touch('release', nx, ny);
      wx = dx > 0 ? LO + 2000 : dx < 0 ? HI - 2000 : wx;
      wy = dy > 0 ? LO + 2000 : dy < 0 ? HI - 2000 : wy;
      touch('contact', wx, wy);
    } else {
      wx = nx; wy = ny;
      touch('contact', wx, wy);
    }
    clearTimeout(wheelIdle);
    wheelIdle = setTimeout(async () => {
      wheelDown = false; wheelResting = true;
      await touch('contact', wx, wy);         // hold still a moment: lift with little momentum
      await sleep(WHEEL_REST_MS);
      if (wheelResting) { wheelResting = false; touch('release', wx, wy); }
    }, WHEEL_IDLE_MS);
  }

  // Sharp text: upstream keeps the canvas at the full 1320x2868 stream size and lets CSS shrink
  // it ~3x with `image-rendering:-webkit-optimize-contrast`, which Chromium treats as
  // nearest-neighbour -> jagged text. Instead draw each frame at the on-screen pixel size with
  // the browser's high-quality resampler. Touch mapping is normalised, so it is unaffected.
  let natW = 0, natH = 0;          // rotated stream footprint (what the canvas shows)

  // Edge's hardware HEVC decoder (Media Foundation) reports every frame as 1280x720 -- a
  // default -- instead of the stream's real portrait size, so the viewer drew the top 720 rows
  // of a 1328x2896 buffer as a "landscape" strip. Read the true visible size from the SPS
  // (pic size minus conformance window) and re-wrap frames whose reported size is wrong.
  let spsW = 0, spsH = 0;
  function parseSpsSize(b64) {
    const hv = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    let p = 22, n = hv[p++], sps = null;
    for (let a = 0; a < n && !sps; a++) {
      const type = hv[p] & 0x3f; p++;
      const cnt = (hv[p] << 8) | hv[p + 1]; p += 2;
      for (let k = 0; k < cnt; k++) {
        const len = (hv[p] << 8) | hv[p + 1]; p += 2;
        if (type === 33 && !sps) sps = hv.subarray(p, p + len);
        p += len;
      }
    }
    if (!sps) return null;
    const rb = [];                                   // strip emulation-prevention bytes
    for (let i = 2, z = 0; i < sps.length; i++) {
      if (z >= 2 && sps[i] === 3) { z = 0; continue; }
      z = sps[i] === 0 ? z + 1 : 0; rb.push(sps[i]);
    }
    let bit = 0;
    const u = (k) => { let v = 0; for (let i = 0; i < k; i++, bit++) v = v * 2 + ((rb[bit >> 3] >> (7 - (bit & 7))) & 1); return v; };
    const ue = () => { let z = 0; while (u(1) === 0 && z < 32) z++; return (1 << z) - 1 + u(z); };
    u(4); const maxSub = u(3); u(1);
    u(88); u(8);                                     // general profile_tier_level
    const subP = [], subL = [];
    for (let i = 0; i < maxSub; i++) { subP.push(u(1)); subL.push(u(1)); }
    if (maxSub > 0) for (let i = maxSub; i < 8; i++) u(2);
    for (let i = 0; i < maxSub; i++) { if (subP[i]) u(88); if (subL[i]) u(8); }
    ue();                                            // sps_seq_parameter_set_id
    const chroma = ue(); if (chroma === 3) u(1);
    let w = ue(), h = ue();
    if (u(1)) {                                      // conformance window
      const sw = (chroma === 1 || chroma === 2) ? 2 : 1, sh = chroma === 1 ? 2 : 1;
      const l = ue(), r = ue(), t = ue(), bo = ue();
      w -= sw * (l + r); h -= sh * (t + bo);
    }
    return (w > 0 && h > 0) ? [w, h] : null;
  }
  async function loadSps() {
    try {
      const j = await (await fetch('/codec')).json();
      const s = j.description && parseSpsSize(j.description);
      if (s) { [spsW, spsH] = s; log('stream size from SPS: ' + spsW + 'x' + spsH); }
    } catch (e) {}
  }
  loadSps();
  setInterval(loadSps, 15000);
  // The HD stream is HEVC. Windows decodes it only with a hardware decoder plus Microsoft's
  // "HEVC Video Extensions"; without them the screen would just stay black, so say why.
  (async () => {
    let ok = false;
    try { ok = !!window.VideoDecoder && (await VideoDecoder.isConfigSupported({codec: 'hev1.1.6.L150.B0'})).supported; }
    catch (e) {}
    if (ok) return;
    const d = document.createElement('div');
    d.style.cssText = 'position:fixed;left:50%;top:40%;transform:translate(-50%,-50%);z-index:99;max-width:300px;' +
      'background:#1c1c20;color:#eee;border:1px solid #3a3a40;border-radius:14px;padding:16px;font:13px system-ui;text-align:center';
    d.innerHTML = '<b>This PC cannot decode the HD video (HEVC).</b><br><br>Install <i>HEVC Video Extensions</i> ' +
      'from the Microsoft Store, then reopen iDesktop.<br><br><button class="btn">Open the Store page</button>';
    d.querySelector('button').onclick = () => { location.href = 'ms-windows-store://pdp/?ProductId=9NMZLZ57R3T7'; };
    document.body.appendChild(d);
  })();  // the stream restarts at a new size after rotation / reconnect

  function fixFrame(f) {
    // Per frame: iOS switches the buffer to landscape when the foreground app rotates, so the
    // true size can change at any time. Prefer the SPS size when it fits this frame's buffer,
    // else the coded size (this stream has no conformance cropping).
    let w = f.codedWidth, h = f.codedHeight;
    if (spsW && spsH && spsW <= w && spsH <= h && (spsW > spsH) === (w > h)) { w = spsW; h = spsH; }
    if (f.displayWidth === w && f.displayHeight === h) return null;
    try {
      return new VideoFrame(f, {visibleRect: {x: 0, y: 0, width: w, height: h}, displayWidth: w, displayHeight: h});
    } catch (e) { return null; }
  }

  let lastDrawAt = Date.now();
  window.drawFrame = function (orig) {
    lastDrawAt = Date.now();
    const fixed = fixFrame(orig);
    try { drawFixed(fixed || orig); } finally { if (fixed) fixed.close(); }
  };
  function drawFixed(f) {
    const sideways = Math.abs(visualRotation % 180) === 90;
    const fw = f.displayWidth, fh = f.displayHeight;
    const tW = sideways ? fh : fw, tH = sideways ? fw : fh;
    if (tW !== natW || tH !== natH) { natW = tW; natH = tH; window.fitCanvasToViewport(); }
    const dpr = window.devicePixelRatio || 1;
    const cssW = parseFloat(canvas.style.width) || tW, cssH = parseFloat(canvas.style.height) || tH;
    const bw = Math.max(1, Math.min(tW, Math.round(cssW * dpr)));
    const bh = Math.max(1, Math.min(tH, Math.round(cssH * dpr)));
    if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    const srcW = _cropSrcW || fw, srcH = _cropSrcH || fh;
    const fpW = sideways ? bh : bw, fpH = sideways ? bw : bh;
    ctx.save();
    ctx.translate(bw / 2, bh / 2);
    if (visualRotation) ctx.rotate(visualRotation * Math.PI / 180);
    ctx.drawImage(f, 0, 0, srcW, srcH, -fpW / 2, -fpH / 2, fpW, fpH);
    ctx.restore();
  };

  // Screenshots: save the latest frame at full stream resolution, not the window-sized canvas.
  function fullResShot(e) {
    if (!lastFrame) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const fixed = fixFrame(lastFrame), src = fixed || lastFrame;
    const sideways = Math.abs(visualRotation % 180) === 90;
    const fw = src.displayWidth, fh = src.displayHeight;
    const c = document.createElement('canvas');
    c.width = sideways ? fh : fw; c.height = sideways ? fw : fh;
    const g = c.getContext('2d');
    g.translate(c.width / 2, c.height / 2);
    if (visualRotation) g.rotate(visualRotation * Math.PI / 180);
    g.drawImage(src, -fw / 2, -fh / 2);
    if (fixed) fixed.close();
    c.toBlob((blob) => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'iphone-' + new Date().toISOString().replace(/[:.]/g, '-') + '.png';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      toast('Screenshot saved to Downloads');
    }, 'image/png');
  }
  document.getElementById('screenshot').addEventListener('click', fullResShot, true);
  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey && !e.altKey && e.key.toLowerCase() === 'p') fullResShot(e);
  }, true);

  // Phone-first sizing: replace the viewer's fit so the device fills the window height
  // (minus our toolbar) at the stream's true aspect ratio, in any window size or rotation.
  window.fitCanvasToViewport = function () {
    // No video yet (e.g. iOS refusing to stream during a call): still draw the floating phone, at
    // the stream's size from the SPS or an iPhone's usual portrait shape, so it isn't just empty.
    if ((!natW || !natH) && stored('ext-compact') !== '0') { natW = spsW || 1320; natH = spsH || 2868; }
    if (!natW || !natH) return;
    try { if (compactLayout()) return; } catch (e) { report('compactLayout: ' + e.stack); }
    const frameOn = document.body.classList.contains('frame-on');
    const bezel = frameOn ? 1.07 : 1;
    const big = document.body.classList.contains('ext-big');
    const top = big ? 0 : document.getElementById('topbar').getBoundingClientRect().height;
    const bottom = big ? 0 : row.getBoundingClientRect().height;
    const stacked = window.matchMedia('(max-width: 900px)').matches;
    const trays = stacked ? 0 : ['left-tray', 'right-tray']
      .map(id => document.getElementById(id)?.getBoundingClientRect().width || 0).reduce((a, b) => a + b, 0);
    const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
    const pad = big ? 0 : 40;
    const availW = (vw - (big ? 0 : trays) - pad) / bezel;
    const availH = (vh - top - bottom - pad) / bezel;
    const z = (typeof zoom === 'number' ? zoom : 1);
    const scale = Math.min(availW / natW, availH / natH) * z;
    const w = natW * scale, h = natH * scale;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    const shortSide = Math.min(w, h);
    const fs = document.getElementById('device-frame').style;
    fs.setProperty('--screen-r', (shortSide * 0.125) + 'px');
    fs.setProperty('--bezel', Math.max(5, shortSide * 0.035) + 'px');
    fs.setProperty('--hw-t', Math.max(3, shortSide * 0.014) + 'px');
  };
  window.addEventListener('resize', () => {
    window.fitCanvasToViewport();
    if (lastFrame) { try { window.drawFrame(lastFrame); } catch (e) {} }  // re-render at the new size now
  });
  setTimeout(() => window.fitCanvasToViewport(), 300);

  // ---- Floating iPhone. The browser window is clipped (by the helper, winshape.py) to the phone
  // body, its side buttons and the floating controls; everything else shows the desktop. Drag the
  // phone's rim to move it. The round button beside it (or Ctrl+M, or right-click the rim) shows
  // the floating command buttons.
  const compactPref = () => stored('ext-compact') !== '0';
  const extPhone = document.createElement('div'); extPhone.id = 'ext-phone';
  const extBezel = document.createElement('div'); extBezel.id = 'ext-bezel';
  const camCtl = document.createElement('div'); camCtl.id = 'ext-camctl';   // Camera Control (cosmetic)
  const extCtl = document.createElement('div'); extCtl.id = 'ext-ctl';
  extPhone.append(extBezel, camCtl); document.body.append(extPhone, extCtl);
  const moved = [];   // [node, parent, nextSibling] so the full layout can be put back
  const moveInto = (node, dest) => { if (!node || node.parentNode === dest) return;
                                     moved.push([node, node.parentNode, node.nextSibling]); dest.appendChild(node); };
  const restoreMoved = () => { while (moved.length) { const [n, p, s] = moved.pop(); p.insertBefore(n, s && s.parentNode === p ? s : null); } };
  const winCmd = (cmd) => fetch(EXT + '/window/cmd', {method: 'POST', body: JSON.stringify({cmd})}).catch(() => {});
  const ICON = {
    home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    apps: '<rect x="4" y="4" width="11" height="14" rx="2"/><path d="M19 7v12a2 2 0 0 1-2 2H9"/>',
    back: '<path d="M15 5l-7 7 7 7"/>',
    cc: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    siri: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    zin: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5M8.5 11h5M11 8.5v5"/>',
    zout: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5M8.5 11h5"/>',
    shot: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    big: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    desk: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    sound: '<path d="M4 10v4h4l5 4V6L8 10z"/><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11"/>',
    pc: '<rect x="3" y="4" width="13" height="10" rx="1.5"/><path d="M7 18h5M9.5 14v4"/><rect x="18" y="8" width="3.5" height="10" rx="1"/>',
    plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>',
    layout: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    min: '<path d="M6 18h12"/>', close: '<path d="M6 6l12 12M18 6L6 18"/>',
    auto: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 8V4.5M9.5 13h.01M14.5 13h.01M2.5 12.5v3M21.5 12.5v3"/><circle cx="12" cy="4" r="1"/>',
    dim: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    unplug: '<path d="M7 7l10 10M9 4v4M15 4v4M6 8h12v3a6 6 0 0 1-12 0zM12 17v4"/>',
    kbd: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    more: '<circle cx="5.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18.5" cy="12" r="1.2"/>',
  };
  const svg = (k) => '<svg viewBox="0 0 24 24">' + ICON[k] + '</svg>';
  const soundBtnUp = document.getElementById('sound-toggle');
  const upClick = (sel) => () => document.querySelector(sel)?.click();
  const soundOn = () => /disable/i.test(soundBtnUp?.textContent || '');
  const groups = [
    [['home', 'Home', upClick('#bottom-row [data-btn="home"]')],
     ['apps', 'App Switcher', () => gestures['App Switcher']()],
     ['back', 'Back', () => gestures['Back']()],
     ['cc', 'Control Center', () => gestures['Control Center']()],
     ['bell', 'Notifications', () => gestures['Notifications']()],
     ['search', 'Spotlight', () => gestures['Spotlight']()],
     ['siri', 'Siri', upClick('#bottom-row [data-btn="siri"]')],
     ['kbd', () => kbdOn ? 'On-screen keyboard: on' : 'On-screen keyboard: off', () => toggleKeyboard()]],
    [['zin', 'Zoom in', () => pinch(0.5, 0.5, 1.8)],
     ['zout', 'Zoom out', () => pinch(0.5, 0.5, 0.5)],
     ['shot', 'Screenshot', () => fullResShot({preventDefault() {}, stopImmediatePropagation() {}})],
     ['desk', () => deskOn ? 'Exit Desktop Mode' : 'Desktop Mode', () => setDesktop(!deskOn)]],
    [['sound', () => soundOn() ? 'PC sound: on' : 'PC sound: off',
      () => { soundBtnUp?.click(); setTimeout(refreshLabels, 400); }],
     ['pc', 'Sound on PC only', async () => {
       toast('Setting the phone volume to 1…');
       try { const r = await fetch(EXT + '/volume', {method: 'POST', body: '{}'});
             toast(r.ok ? 'Phone volume is 1 - the sound plays on the PC' : 'Could not set the volume'); }
       catch (e) { toast('Helper not running'); }
     }],
     ['dim', () => dimmed ? 'Undim phone' : 'Dim phone', () => toggleDim()],
     ['auto', () => auto.wanted ? 'Automation: on' : 'Automation: off', () => setAutomation(!auto.wanted)]],
    [['plus', 'Bigger', () => resizeL(1.1)],
     ['minus', 'Smaller', () => resizeL(1 / 1.1)],
     ['layout', 'Classic window', () => { store('ext-compact', '0'); setMenu(false); window.fitCanvasToViewport(); }],
     ['unplug', 'Disconnect', () => disconnect()],
     ['min', 'Minimize', () => winCmd('min')],
     ['close', 'Quit', () => winCmd('close'), 'danger']],
  ];
  // Automation = WebDriverAgent (auto-rotate, pinch, "Sound on PC only"). While it runs iOS shows
  // "Automation Running"; holding both volume buttons on the phone also turns it off.
  const CTL = 'http://127.0.0.1:__CTL_PORT__/automation';
  let auto = {available: false, wanted: false, running: false};
  async function pollAutomation() {
    const prev = auto;
    try {
      auto = await (await fetch(CTL, {cache: 'no-store'})).json();
      if (prev.wanted && !auto.wanted) toast('Automation off - auto-rotate and pinch paused');
    } catch (e) {}
    pills.find(p => p.icon === 'auto')?.el.classList.toggle('hidden-pill', !auto.available);
    refreshLabels();
    if (prev.available !== auto.available) window.fitCanvasToViewport();   // re-shape the controls
  }
  async function setAutomation(on) {
    try {
      auto = await (await fetch(CTL, {method: 'POST', body: JSON.stringify({on})})).json();
      toast(on ? 'Automation on - auto-rotate and pinch available' :
                 'Automation off - no overlay on the phone; auto-rotate and pinch paused');
    } catch (e) { toast('Could not reach the stream'); }
    refreshLabels();
  }
  setInterval(pollAutomation, 3000);
  setTimeout(pollAutomation, 500);
  // During a phone/FaceTime call iOS refuses to (re)start the stream (code 9022); say so on the
  // phone's screen instead of a bare "Stream offline". Upstream keeps retrying, so HD comes back
  // by itself after the call.
  const overlay = document.getElementById('offline-overlay');
  const overlayText = overlay?.querySelector('span');
  const defaultOverlay = overlayText?.textContent;
  setInterval(async () => {
    try {
      const st = await (await fetch(CTL.replace('/automation', '/status'), {cache: 'no-store'})).json();
      streamStalled = !!st.stalled; callBlocked = !!st.call_blocked;
      if (!overlayText) return;
      if (st.call_blocked) {
        overlayText.textContent = 'Paused by iOS during your call - HD comes back by itself after it ends.';
        if (!lastFrame) overlay.classList.remove('hidden');
      } else if (overlayText.textContent !== defaultOverlay) {
        overlayText.textContent = defaultOverlay;
      }
    } catch (e) {}
  }, 3000);
  setTimeout(() => window.fitCanvasToViewport(), 800);   // phone shape even before the first frame

  // Lite mode: when HD video stops (iOS sends none during calls) and Automation is on,
  // show WebDriverAgent's MJPEG screen feed in the phone's screen instead of "Stream offline";
  // touch keeps going through the same HID path. Back to HD as soon as frames return.
  const MJPEG = 'http://127.0.0.1:__MJPEG_PORT__/';
  const basic = document.createElement('img');
  basic.id = 'ext-basic'; basic.alt = '';
  basic.style.cssText = 'position:fixed;display:none;object-fit:fill;pointer-events:none;z-index:15;background:#000';
  document.body.appendChild(basic);
  let basicOn = false, streamStalled = false, callBlocked = false, basicSrcAt = 0;
  // Reveal the basic feed only once its first picture has arrived (else it's just a black box);
  // until then the "paused during your call" message stays visible.
  const showBasic = (on) => { basic.style.visibility = on ? 'visible' : 'hidden';
                              document.body.classList.toggle('ext-basic-on', on); };
  basic.addEventListener('load', () => { if (basicOn) showBasic(true); });
  basic.addEventListener('error', () => showBasic(false));
  function startBasicFeed() {
    // WebDriverAgent needs a few seconds after (re)starting before its feed answers.
    basicSrcAt = Date.now();
    fetch(EXT + '/mjpeg', {method: 'POST', body: '{}'}).catch(() => {});
    basic.src = MJPEG + '?t=' + basicSrcAt;
  }
  function placeBasic() {
    const r = canvas.getBoundingClientRect();
    Object.assign(basic.style, {left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px',
                                borderRadius: canvas.style.borderRadius || '0'});
  }
  setInterval(() => {
    const quiet = Date.now() - lastDrawAt;
    // A still screen also sends no frames; only fall back when the server saw a real stall/call.
    if (!basicOn && quiet > 3000 && streamStalled && auto.running && document.visibilityState === 'visible') {
      basicOn = true;
      basic.style.display = 'block'; showBasic(false);
      startBasicFeed();
      toast('HD paused by iOS during the call - Lite mode: still live and controllable, at a lower frame rate');
    } else if (basicOn && (quiet < 1500 || !auto.running || !streamStalled)) {
      basicOn = false;
      basic.removeAttribute('src');               // closes the MJPEG connection
      basic.style.display = 'none'; showBasic(false);
    }
    if (basicOn) {
      placeBasic();
      // Chromium doesn't reliably fire 'load' for an MJPEG stream; a decoded picture has a size.
      if (basic.naturalWidth > 0) showBasic(true);
      else if (Date.now() - basicSrcAt > 3000) startBasicFeed();   // not answering yet: retry
    }
    if (disconnected) placeDisc();   // follows the phone when it's dragged
    const basicShowing = basicOn && basic.style.visibility === 'visible';
    const showPaused = !disconnected && streamStalled && quiet > 3000 && !basicShowing;
    if (showPaused) {
      paused.textContent = callBlocked
        ? 'Paused by iOS during your call. HD comes back by itself after it ends.' +
          (auto.available && !auto.running ? ' Turn on Automation (in the controls) to keep using it in Lite mode meanwhile.' :
           auto.running ? ' Switching to Lite mode…' : '')
        : 'Reconnecting to the screen…';
    }
    placeOver(paused, showPaused);
  }, 1000);
  // iOS hides its keyboard while ours (a hardware keyboard to iOS) is attached; Eject toggles it.
  // iOS doesn't report the setting, but it sticks until toggled again, so track it here.
  let kbdOn = stored('ext-kbd') === '1';
  const toggleKeyboard = () => {
    kbdOn = !kbdOn; store('ext-kbd', kbdOn ? '1' : '0');
    toast(kbdOn ? 'On-screen keyboard on - the iPhone shows its keyboard in text fields'
                : 'On-screen keyboard off - type with your PC keyboard');
    refreshLabels();
    return fetch('/button', {method: 'POST', headers: {'Content-Type': 'application/json'},
                             body: JSON.stringify({name: 'keyboard'})}).catch(() => {});
  };
  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey && !e.altKey && e.key.toLowerCase() === 'k') { e.preventDefault(); e.stopImmediatePropagation(); toggleKeyboard(); }
  }, true);
  // Buttons that only work while Automation (WebDriverAgent) runs: shown dimmed when it's off.
  const NEEDS_AUTO = new Set(['zin', 'zout', 'pc']);
  // Dim the phone's own screen (keyboard brightness keys); the captured picture isn't affected.
  let dimmed = stored('ext-dim') === '1';
  async function press(name, n) {
    for (let i = 0; i < n; i++) {
      await fetch('/button', {method: 'POST', headers: {'Content-Type': 'application/json'},
                              body: JSON.stringify({name})}).catch(() => {});
      await sleep(60);
    }
  }
  async function toggleDim() {
    dimmed = !dimmed; store('ext-dim', dimmed ? '1' : '0'); refreshLabels();
    if (dimmed) { await press('brightness-down', 16); toast('Phone screen dimmed - the mirror stays bright'); }
    else { await press('brightness-up', 8); toast('Phone screen back to medium brightness'); }
  }
  // Disconnect: the stream process ends (phone released); the launcher keeps this window and
  // waits for Reconnect, which goes through the helper because the stream server is gone.
  const disc = document.createElement('div');
  disc.id = 'ext-disc';
  disc.innerHTML = '<div>Disconnected from your iPhone</div><button class="btn" type="button">Reconnect</button>';
  disc.style.cssText = 'position:fixed;display:none;z-index:16;background:#000;color:#ddd;font:14px system-ui;' +
    'flex-direction:column;align-items:center;justify-content:center;gap:14px;text-align:center';
  document.body.appendChild(disc);
  let disconnected = false;
  // Our own "paused" screen: iOS blocks HD during calls; upstream's offline box only shows by its
  // own rules (not once video has played), so the call message could stay invisible.
  const paused = document.createElement('div');
  paused.id = 'ext-paused';
  paused.style.cssText = disc.style.cssText.replace('z-index:16', 'z-index:14') + ';padding:24px;box-sizing:border-box;line-height:1.45';
  document.body.appendChild(paused);
  function placeOver(el, show) {
    const r = canvas.getBoundingClientRect();
    Object.assign(el.style, {left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px',
                             borderRadius: canvas.style.borderRadius || '0', display: show ? 'flex' : 'none'});
  }
  function placeDisc() {
    const r = canvas.getBoundingClientRect();
    Object.assign(disc.style, {left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px',
                               borderRadius: canvas.style.borderRadius || '0', display: disconnected ? 'flex' : 'none'});
  }
  async function disconnect() {
    disconnected = true; setMenu(false); placeDisc();
    disc.querySelector('div').textContent = 'Disconnected from your iPhone';
    try { await fetch(CTL.replace('/automation', '/disconnect'), {method: 'POST', body: '{}'}); } catch (e) {}
  }
  disc.querySelector('button').addEventListener('click', async () => {
    disc.querySelector('div').textContent = 'Reconnecting…';
    try { await fetch(EXT + '/reconnect', {method: 'POST', body: '{}'}); } catch (e) {}
    for (let i = 0; i < 90; i++) {           // wait for the stream server to come back, then reload
      await sleep(1000);
      // Any answer from the page itself means the stream server is back (/codec may still be
      // failing, e.g. during a call, until the video starts).
      try { if ((await fetch('/', {cache: 'no-store'})).ok) { location.reload(); return; } } catch (e) {}
    }
    disc.querySelector('div').textContent = "Couldn't reconnect - is the iPhone nearby and unlocked?";
  });
  window.addEventListener('resize', () => { if (disconnected) placeDisc(); });
  const pills = [];
  // Hover help (also in the README's "Floating controls" table).
  const TIPS = {
    home: 'Go to the home screen (or right-click the screen, Ctrl+H)',
    apps: 'Show recent apps (swipe up and hold)',
    back: 'Swipe in from the left edge - "back" in most apps',
    cc: 'Swipe down from the top-right corner',
    bell: 'Swipe down from the top-left: notifications',
    search: 'Swipe down on the home screen: search',
    siri: 'Hold the side button for Siri (Ctrl+S)',
    kbd: "Show/hide the iPhone's own keyboard in text fields. iOS hides it while your PC keyboard is connected (Ctrl+K). " +
         "If you toggled it on the phone itself, click twice to resync",
    zin: 'Two-finger zoom in at the screen centre (or Ctrl + mouse wheel). Needs Automation',
    zout: 'Two-finger zoom out (or Ctrl + mouse wheel). Needs Automation',
    shot: 'Save a full-resolution screenshot to Downloads (Ctrl+P)',
    desk: 'Extra-small text on the phone: denser, iPad-like layouts. Click again to restore your text size',
    sound: "Play the phone's sound on this PC (on/off)",
    pc: 'Turn the phone volume down to its lowest step: the PC keeps full volume, the phone is nearly silent. Needs Automation',
    auto: 'WebDriverAgent helper: auto-rotate, pinch zoom, "Sound on PC only", and Lite mode during calls. ' +
          'While on, iOS shows "Automation Running" (holding both volume buttons on the phone also turns it off)',
    plus: 'Make the phone bigger (Ctrl+Up)', minus: 'Make the phone smaller (Ctrl+Down)',
    layout: 'Switch to the normal window with toolbar, accessibility and clipboard panels',
    dim: 'Turn the screen of the phone itself down to minimum brightness - the mirror here stays bright. ' +
         'For near-black, also set Settings > Accessibility > Display & Text Size > Reduce White Point',
    unplug: 'Stop mirroring and release the phone (the "Automation Running" notice goes away too). ' +
            'The phone stays on your desktop with a Reconnect button',
    min: 'Minimize iDesktop', close: 'Close iDesktop',
  };
  groups.forEach((g, gi) => g.forEach(([icon, label, fn, cls], i) => {
    const el = document.createElement('div'); el.className = 'ext-pill' + (cls ? ' ' + cls : '');
    if (TIPS[icon]) el.title = TIPS[icon];
    el.innerHTML = svg(icon) + '<span></span>';
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      if (NEEDS_AUTO.has(icon) && !auto.running) {
        toast(auto.available ? 'Needs Automation - turn on "Automation" in this menu first'
                             : 'Needs the optional WebDriverAgent extra (see the README)');
        return;
      }
      fn(); setTimeout(refreshLabels, 60);
    });
    extCtl.appendChild(el); pills.push({el, label, icon, gap: gi > 0 && i === 0});
  }));
  function refreshLabels() {
    for (const p of pills) {
      p.el.querySelector('span').textContent = typeof p.label === 'function' ? p.label() : p.label;
      if (p.icon === 'sound') p.el.classList.toggle('on', soundOn());
      if (p.icon === 'desk') p.el.classList.toggle('on', deskOn);
      if (p.icon === 'auto') p.el.classList.toggle('on', auto.wanted);
      if (p.icon === 'dim') p.el.classList.toggle('on', dimmed);
      if (p.icon === 'kbd') p.el.classList.toggle('on', kbdOn);
      p.el.classList.toggle('needs-auto', NEEDS_AUTO.has(p.icon) && !auto.running);
    }
  }
  const fab = document.createElement('div'); fab.className = 'ext-fab'; fab.title = 'Controls (Ctrl+M)';
  fab.innerHTML = svg('more'); extCtl.appendChild(fab);
  fab.addEventListener('click', (e) => { e.stopPropagation(); setMenu(!menuOpen); });
  { const b = document.createElement('button'); b.className = 'btn'; b.textContent = 'Floating Phone'; b.id = 'ext-to-compact';
    b.title = 'Just the phone, floating on the desktop (Ctrl+M for controls)';
    b.addEventListener('click', () => { store('ext-compact', '1'); window.fitCanvasToViewport(); }); row.appendChild(b); }

  let compactOn = false, menuOpen = false;
  function setMenu(on) {
    menuOpen = on; fab.innerHTML = svg(on ? 'close' : 'more'); refreshLabels(); window.fitCanvasToViewport();
  }
  function resizeL(f) {
    const cur = parseFloat(stored('ext-L')) || Math.min(screen.availHeight * 0.9, 1000);
    store('ext-L', String(Math.round(cur * f))); window.fitCanvasToViewport();
    if (lastFrame) { try { window.drawFrame(lastFrame); } catch (e) {} }
  }
  let layoutBusy = false, layoutNext = null, layoutSent = '', layoutWanted = '';
  async function postLayout(spec) {
    const s = JSON.stringify(spec); layoutWanted = s;
    if (s === layoutSent && !layoutNext) return;
    layoutNext = s;
    if (layoutBusy) return; layoutBusy = true;
    try {
      while (layoutNext) {
        const b = layoutNext; layoutNext = null;
        let ok = false;
        try { const r = await fetch(EXT + '/window/layout', {method: 'POST', body: b});
              ok = r.ok; if (!ok) report('window layout: ' + await r.text()); } catch (e) {}
        if (ok) layoutSent = b;
        else setTimeout(() => { if (layoutWanted === b && layoutSent !== b) postLayout(JSON.parse(b)); }, 1500);  // helper (re)starting
      }
    } finally { layoutBusy = false; }
  }
  const place = (el, x, y, w, h, r) => Object.assign(el.style, {left: x + 'px', top: y + 'px', width: w + 'px',
                                                                  height: h + 'px', borderRadius: r + 'px'});
  // Called first thing by fitCanvasToViewport; true when it handled the layout.
  function compactLayout() {
    const big = document.body.classList.contains('ext-big');
    const want = compactPref() && !big;
    const dpr = window.devicePixelRatio || 1;
    if (want !== compactOn) {
      compactOn = want; document.body.classList.toggle('ext-compact', want);
      if (want) {
        moveInto(canvas, extBezel); moveInto(document.getElementById('offline-overlay'), extBezel);
        for (const hw of document.querySelectorAll('#device-frame .hw')) moveInto(hw, extPhone);
        refreshLabels();
      } else {
        restoreMoved(); menuOpen = false; fab.innerHTML = svg('more'); toastEl.removeAttribute('style');
        canvas.removeAttribute('style');
        for (const hw of document.querySelectorAll('#device-frame .hw'))
          for (const k of ['left', 'top', 'width', 'height', 'display']) hw.style.removeProperty(k);
        const h = Math.round(screen.availHeight * 0.95);
        postLayout(big ? {off: true} : {off: true, dpr, size: [Math.round(h * 0.52), h]});
        setTimeout(() => window.fitCanvasToViewport(), 50);
      }
    }
    if (!want) return false;
    // The window is maximized (see winshape: no DWM shadow/border that way) and clipped to the
    // phone, so the phone's position is just an offset inside the page.
    const full = innerWidth >= screen.availWidth * 0.9;
    const sideways = natW > natH;
    let L = parseFloat(stored('ext-L')) || Math.min(screen.availHeight * 0.88, 1000);
    L = Math.max(320, Math.min(L, sideways ? screen.availWidth * 0.9 : (full ? innerHeight - 6 : screen.availHeight * 0.97)));
    if (bigFloat) L = sideways ? Math.min(innerWidth - 250, (innerHeight - 10) * natW / natH) : innerHeight - 10;
    const RIM = Math.max(3, Math.round(L * 0.0035)), B = Math.max(7, Math.round(L * 0.009)), E = RIM + B;
    let sw, sh;
    if (sideways) { sw = L - 2 * E; sh = sw * natH / natW; } else { sh = L - 2 * E; sw = sh * natW / natH; }
    sw = Math.round(sw); sh = Math.round(sh);
    const pw = sw + 2 * E, ph = sh + 2 * E, sr = Math.round(Math.min(sw, sh) * 0.13), R = sr + E;
    const PAD = 4, FAB = 42, PH = 36, PW = 204, CXR = pw + PAD + 14;   // controls column, relative
    if (full) {
      const maxX = innerWidth - (PAD + CXR + FAB + 4), maxY = innerHeight - ph - 2;
      posX = Math.max(0, Math.min(posX, maxX)); posY = Math.max(0, Math.min(posY, maxY));
    }
    const OX = Math.round(bigFloat ? (innerWidth - pw) / 2 - PAD - 100 : posX);
    const OY = Math.round(bigFloat ? (innerHeight - ph) / 2 : posY), X = OX + PAD;
    place(extPhone, X, OY, pw, ph, R);
    place(extBezel, RIM, RIM, pw - 2 * RIM, ph - 2 * RIM, R - RIM);
    place(canvas, B, B, sw, sh, sr);
    Object.assign(toastEl.style, {left: (X + pw / 2) + 'px', top: (OY + ph - 70) + 'px', bottom: 'auto'});
    const shapes = [{x: X, y: OY, w: pw, h: ph, r: R}];
    // Side buttons on the long edges (portrait: left = action/volume, right = power + Camera Control).
    const side = [];
    for (const hw of extPhone.querySelectorAll('.hw')) {
      const pos = parseFloat(hw.style.getPropertyValue('--pos')) / 100, len = parseFloat(hw.style.getPropertyValue('--len')) / 100;
      side.push([hw, hw.classList.contains('hw-r'), pos, len]);
    }
    side.push([camCtl, true, 0.53, 0.085]);
    for (const [el, right, pos, len] of side) {
      if (sideways || !(pos >= 0)) { el.style.display = 'none'; continue; }
      el.style.display = '';
      const bx = right ? pw - 1 : -3, by = Math.round(ph * pos), bh = Math.round(ph * len);
      Object.assign(el.style, {left: bx + 'px', top: by + 'px', width: '4px', height: bh + 'px'});
      shapes.push({x: X + bx, y: OY + by, w: 4, h: bh, r: 2});
    }
    // Floating controls to the right of the phone.
    const CX = X + CXR;
    Object.assign(extCtl.style, {left: CX + 'px', top: OY + 'px'});
    place(fab, 0, 0, FAB, FAB, FAB / 2);
    shapes.push({x: CX, y: OY, w: FAB, h: FAB, r: FAB / 2});
    let y = FAB + 12;
    for (const p of pills) {
      p.el.classList.toggle('show', menuOpen && !p.el.classList.contains('hidden-pill'));
      if (!p.el.classList.contains('show')) continue;
      if (!menuOpen) continue;
      if (p.gap) y += 10;
      place(p.el, 0, y, PW, PH, PH / 2);
      shapes.push({x: CX, y: OY + y, w: PW, h: PH, r: PH / 2});
      y += PH + 6;
    }
    // Big Screen: the whole (maximized) window is shown, black around the phone.
    if (bigFloat) shapes.splice(0, shapes.length, {x: 0, y: 0, w: innerWidth, h: innerHeight, r: 0});
    postLayout({dpr, maximize: true, shapes});
    return true;
  }
  // Back from the background/minimized: re-send the shape so the helper re-clips and repaints the
  // whole window (Edge can otherwise show a stale copy of the old picture next to the new one).
  const reshape = () => { if (compactOn && document.visibilityState === 'visible') { layoutSent = ''; window.fitCanvasToViewport(); } };
  document.addEventListener('visibilitychange', () => setTimeout(reshape, 50));
  window.addEventListener('focus', () => setTimeout(reshape, 50));
  // Re-send the shape now and then so a restarted helper picks it up again.
  setInterval(() => { if (compactOn) { layoutSent = ''; window.fitCanvasToViewport(); } }, 10000);
  // Drag the phone's rim / bezel to move it (it moves inside the maximized, clipped window).
  let posX = 40, posY = 8;
  { const sp = (stored('ext-pos') || '').split(',').map(Number); if (sp.length === 2 && sp.every(isFinite)) [posX, posY] = sp; }
  let dragId = null, dLX = 0, dLY = 0, dragRaf = 0;
  const isRim = (t) => t === extPhone || t === extBezel || t === camCtl;
  extPhone.addEventListener('pointerdown', (e) => {
    if (!isRim(e.target) || e.button !== 0 || bigFloat) return;
    dragId = e.pointerId; extPhone.setPointerCapture(dragId); dLX = e.clientX; dLY = e.clientY; e.preventDefault();
  });
  extPhone.addEventListener('pointermove', (e) => {
    if (e.pointerId !== dragId) return;
    posX += e.clientX - dLX; posY += e.clientY - dLY; dLX = e.clientX; dLY = e.clientY;
    if (!dragRaf) dragRaf = requestAnimationFrame(() => { dragRaf = 0; window.fitCanvasToViewport(); });
  });
  const endDrag = (e) => { if (e.pointerId !== dragId) return; dragId = null;
                           store('ext-pos', Math.round(posX) + ',' + Math.round(posY)); };
  extPhone.addEventListener('pointerup', endDrag); extPhone.addEventListener('pointercancel', endDrag);
  extPhone.addEventListener('contextmenu', (e) => { if (!isRim(e.target)) return; e.preventDefault(); setMenu(!menuOpen); });
  window.addEventListener('keydown', (e) => {
    if (!e.ctrlKey || e.altKey) return;
    if (e.key.toLowerCase() === 'm') { e.preventDefault(); e.stopImmediatePropagation(); if (compactOn) setMenu(!menuOpen); }
    else if (compactOn && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault(); e.stopImmediatePropagation(); resizeL(e.key === 'ArrowUp' ? 1.1 : 1 / 1.1);
    }
  }, true);

  // iPad-style shortcuts: the Windows key belongs to Windows, so Alt plays Cmd on the phone
  // (Alt+Space = Spotlight, Alt+H = Home, Alt+C/V/X/Z/A/F/T in apps that support them).
  // Alt+Tab stays with Windows.
  CODE_TO_HID.AltLeft = 0xE3; CODE_TO_HID.AltRight = 0xE7;
  // Stop Alt from focusing the browser's menu while it's being used as Cmd.
  window.addEventListener('keyup', (e) => { if (e.key === 'Alt' && document.activeElement === canvas) e.preventDefault(); }, true);

  // Self-update: the server rebuilds this page from viewer_layer.py on every load; when it
  // changes, reload so new buttons/fixes appear without touching the stream session.
  let pageText = null, lastInputAt = Date.now();
  for (const ev of ['pointerdown', 'pointermove', 'pointerup', 'keydown', 'wheel'])
    window.addEventListener(ev, () => { lastInputAt = Date.now(); }, {capture: true, passive: true});
  // Back from the background: ask for a keyframe right away (~0.1-0.3 s) instead of waiting for
  // the encoder's next one; /pli is upstream's lightweight recovery (no stream restart).
  let pliAt = 0;
  const freshFrame = () => {
    if (document.visibilityState !== 'visible' || Date.now() - pliAt < 2000) return;
    pliAt = Date.now();
    fetch('/pli', {method: 'POST', cache: 'no-store'}).catch(() => {});
  };
  document.addEventListener('visibilitychange', freshFrame);
  window.addEventListener('focus', freshFrame);
  setInterval(async () => {
    try {
      const t = await (await fetch('/', {cache: 'no-store'})).text();
      if (pageText === null) pageText = t;
      // Don't reload mid-touch - unless the "touch" is stale (a drag that ended outside the window
      // can leave activePointer set, which used to block updates for good).
      else if (t !== pageText && (!activePointer || Date.now() - lastInputAt > 10000)) location.reload();
    } catch (e) {}
  }, 4000);

  // Auto-rotate: follow the foreground app's orientation (the stream itself stays portrait).
  // window.EXT_ROT_SIGN flips the left/right mapping if it turns out backwards.
  window.EXT_ROT_SIGN = window.EXT_ROT_SIGN || 1;
  let autoRot = true;
  setInterval(async () => {
    if (!autoRot) return;
    try {
      const o = await (await fetch(EXT + '/orientation', {cache: 'no-store'})).json();
      if (!o.ok) return;
      let deg = 0;
      if (o.landscape) deg = (o.z === 270 ? -90 : 90) * window.EXT_ROT_SIGN;  // verified on iPhone17,2: z=270 -> -90
      if (deg !== visualRotation) { setVisualRotation(deg); window.fitCanvasToViewport(); }
    } catch (e) {}
  }, 600);

  // Turn on two-way clipboard sync once the viewer has wired its buttons.
  setTimeout(() => {
    const b = document.getElementById('clipboard-sync');
    if (b && /off/i.test(b.textContent)) b.click();
  }, 1500);
})();
</script>
"""
