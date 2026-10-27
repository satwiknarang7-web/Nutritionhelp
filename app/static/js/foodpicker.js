/* Reusable food picker: local + external search, barcode scanning, serving selection. */
(function () {
  const NT = window.NT;
  const ZXING_URL = 'https://cdn.jsdelivr.net/npm/@zxing/browser@0.1.5/umd/zxing-browser.min.js';

  function servingOptions(food) {
    const opts = (food.servings || []).map((s) => ({ label: s.label, grams: s.grams }));
    opts.push({ label: 'grams / ml', grams: 1 });
    return opts;
  }

  function sourceBadge(food) {
    if (food.source === 'custom') return '<span class="badge">mine</span>';
    if (food.source === 'openfoodfacts') return '<span class="badge">packaged</span>';
    if (food.source === 'usda') return '<span class="badge">USDA</span>';
    if (food.diet === 'non-veg') return '<span class="badge" title="Non-vegetarian">🔴</span>';
    if (food.diet === 'egg') return '<span class="badge" title="Contains egg">🟡</span>';
    if (food.diet === 'veg') return '<span class="badge" title="Vegetarian">🟢</span>';
    return '';
  }

  function resultItem(food) {
    const s = (food.servings || [])[0];
    const kcal = s ? (food.per_100g.energy * s.grams) / 100 : food.per_100g.energy;
    const per = s ? s.label : '100 g';
    return `<li><button type="button" class="result" data-id="${NT.esc(food.id)}">
      <span class="r-left"><span class="food-emoji">${NT.emoji(food)}</span>
        <span><span class="r-name">${NT.esc(food.name)}</span> ${sourceBadge(food)}
        <div class="r-meta">${NT.esc(food.category || '')}</div></span></span>
      <span class="r-kcal">${NT.fmt(kcal, 0)} kcal / ${NT.esc(per)}</span></button></li>`;
  }

  /**
   * mount(container, { onPick(selection), submitLabel, showMeal })
   * selection = { food, quantity, unit, grams, meal }
   */
  NT.mountFoodPicker = function (root, opts = {}) {
    root.innerHTML = `
      <div class="row" style="align-items:center">
        <div style="flex:1 1 220px;position:relative">
          <label class="sr-only" for="fp-q">Search foods</label>
          <input id="fp-q" type="search" placeholder="Search: dosa, rajma chawal, Amul butter…" autocomplete="off">
        </div>
        <button type="button" class="btn" data-act="scan" style="flex:0 0 auto" title="Scan a barcode">▮▯▮ Scan barcode</button>
      </div>
      <div data-el="scanner" class="hidden" style="margin-top:10px">
        <div class="scan-wrap scanning"><video playsinline muted style="width:100%;max-height:240px;border-radius:12px;background:#000"></video></div>
        <div class="row" style="margin-top:8px">
          <input data-el="barcode" inputmode="numeric" placeholder="…or type the barcode number">
          <button type="button" class="btn" data-act="lookup" style="flex:0 0 auto">Look up</button>
          <button type="button" class="btn btn-ghost" data-act="stopscan" style="flex:0 0 auto">Close</button>
        </div>
      </div>
      <div data-el="list">
        <ul class="results" data-el="local"></ul>
        <div data-el="ext-head" class="small muted hidden" style="margin-top:10px">Packaged & global foods</div>
        <ul class="results" data-el="external"></ul>
        <div data-el="hint" class="small muted" style="margin-top:8px">570+ Indian & everyday foods built in, plus packaged foods and USDA data. Can't find it? Try the Photo or Describe tabs, or create your own food.</div>
      </div>
      <div data-el="detail" class="hidden"></div>`;

    const $ = (sel) => root.querySelector(`[data-el="${sel}"]`);
    const q = root.querySelector('#fp-q');
    const foods = new Map();
    let searchTimer, extTimer, seq = 0, stream, zxingControls;

    function renderList(el, list) {
      list.forEach((f) => foods.set(f.id, f));
      el.innerHTML = list.map(resultItem).join('');
    }

    async function search(term) {
      const my = ++seq;
      $('detail').classList.add('hidden');
      $('list').classList.remove('hidden');
      if (term.trim().length < 1) { $('local').innerHTML = ''; $('external').innerHTML = ''; $('ext-head').classList.add('hidden'); return; }
      try {
        const local = await NT.api(`/api/foods/search?q=${encodeURIComponent(term)}`);
        if (my !== seq) return;
        renderList($('local'), local);
      } catch (e) { NT.toast(e.message); }
      clearTimeout(extTimer);
      if (term.trim().length < 3) return;
      extTimer = setTimeout(async () => {
        $('ext-head').classList.remove('hidden');
        $('external').innerHTML = '<li class="thinking small"><span class="spinner"></span> Searching packaged & global foods…</li>';
        try {
          const ext = await NT.api(`/api/foods/search/external?q=${encodeURIComponent(term)}`);
          if (my !== seq) return;
          renderList($('external'), ext);
          if (!ext.length) $('external').innerHTML = '<li class="empty">No packaged or global matches.</li>';
        } catch (e) {
          if (my === seq) $('external').innerHTML = `<li class="empty">${NT.esc(e.message)}</li>`;
        }
      }, 450);
    }

    q.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => search(q.value), 160); });

    root.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-id]');
      if (btn && foods.has(btn.dataset.id)) return showDetail(foods.get(btn.dataset.id));
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (act === 'scan') startScan();
      if (act === 'stopscan') stopScan();
      if (act === 'lookup') lookupBarcode($('barcode').value);
      if (act === 'back') { $('detail').classList.add('hidden'); $('list').classList.remove('hidden'); q.focus(); }
    });

    function showDetail(food) {
      const options = servingOptions(food);
      const meals = ['breakfast', 'lunch', 'dinner', 'snacks'];
      const d = $('detail');
      d.innerHTML = `
        <button type="button" class="btn btn-ghost btn-sm" data-act="back">← Back to results</button>
        <h3 style="margin-top:10px;display:flex;gap:12px;align-items:center"><span class="food-tile" style="--tile:${NT.tile(food)}">${NT.emoji(food)}</span>${NT.esc(food.name)} ${sourceBadge(food)}</h3>
        <div class="row">
          <div class="field"><label for="fp-qty">Quantity</label><input id="fp-qty" type="number" min="0.1" step="0.5" value="${options.length > 1 ? 1 : 100}" inputmode="decimal"></div>
          <div class="field"><label for="fp-unit">Serving</label><select id="fp-unit">${options.map((o, i) => `<option value="${i}">${NT.esc(o.label)}${o.grams !== 1 ? ` (${NT.fmt(o.grams, 0)} g)` : ''}</option>`).join('')}</select></div>
          ${opts.showMeal ? `<div class="field"><label for="fp-meal">Meal</label><select id="fp-meal">${meals.map((m) => `<option value="${m}" ${m === opts.defaultMeal?.() ? 'selected' : ''}>${m[0].toUpperCase() + m.slice(1)}</option>`).join('')}</select></div>` : ''}
        </div>
        <div class="preview" data-el="preview"></div>
        <button type="button" class="btn btn-primary btn-block" data-el="submit">${NT.esc(opts.submitLabel || 'Add')}</button>`;
      $('list').classList.add('hidden');
      d.classList.remove('hidden');
      const qty = d.querySelector('#fp-qty'), unit = d.querySelector('#fp-unit');
      const grams = () => (Number(qty.value) || 0) * options[Number(unit.value)].grams;
      const update = () => {
        const g = grams(), p = food.per_100g;
        $('preview').innerHTML = [['energy', 'kcal', 0], ['protein', 'protein g', 1], ['carbs', 'carbs g', 1], ['fat', 'fat g', 1]]
          .map(([k, l, dg]) => `<div><strong>${NT.fmt((p[k] || 0) * g / 100, dg)}</strong><span>${l}</span></div>`).join('');
      };
      unit.addEventListener('change', () => { if (options[Number(unit.value)].grams === 1 && Number(qty.value) < 5) qty.value = 100; update(); });
      qty.addEventListener('input', update);
      update();
      $('submit').addEventListener('click', async () => {
        const g = grams();
        if (!(g > 0)) return NT.toast('Enter a quantity');
        const o = options[Number(unit.value)];
        $('submit').disabled = true;
        try {
          await opts.onPick({
            food, grams: g, quantity: Number(qty.value), unit: o.grams === 1 ? 'g' : o.label,
            meal: d.querySelector('#fp-meal')?.value,
          });
        } finally { $('submit').disabled = false; }
      });
      qty.focus();
      qty.select();
    }

    async function lookupBarcode(code) {
      const digits = String(code || '').replace(/\D/g, '');
      if (digits.length < 6) return NT.toast('Enter a valid barcode');
      stopScan();
      try {
        const food = await NT.api(`/api/foods/barcode/${digits}`);
        foods.set(food.id, food);
        showDetail(food);
      } catch (e) { NT.toast(e.message, 4000); }
    }

    async function startScan() {
      $('scanner').classList.remove('hidden');
      const video = $('scanner').querySelector('video');
      if (!navigator.mediaDevices?.getUserMedia) { NT.toast('Camera not available - type the barcode instead.'); return; }
      try {
        if ('BarcodeDetector' in window) {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
          video.srcObject = stream;
          await video.play();
          const detector = new window.BarcodeDetector({ formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e'] });
          const tick = async () => {
            if (!stream) return;
            try {
              const codes = await detector.detect(video);
              if (codes.length) return lookupBarcode(codes[0].rawValue);
            } catch (_) { /* frame not ready */ }
            requestAnimationFrame(tick);
          };
          tick();
        } else {
          if (!window.ZXingBrowser) await loadScript(ZXING_URL);
          const reader = new window.ZXingBrowser.BrowserMultiFormatReader();
          zxingControls = await reader.decodeFromVideoDevice(undefined, video, (result) => {
            if (result) lookupBarcode(result.getText());
          });
        }
      } catch (e) {
        NT.toast('Could not open the camera - type the barcode instead.');
      }
    }

    function stopScan() {
      if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
      if (zxingControls) { zxingControls.stop(); zxingControls = null; }
      $('scanner').classList.add('hidden');
    }

    function loadScript(src) {
      return new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = src; s.onload = resolve; s.onerror = reject;
        document.head.appendChild(s);
      });
    }

    return {
      search: (term) => { q.value = term; search(term); q.focus(); },
      focus: () => q.focus(),
      reset: () => { stopScan(); q.value = ''; search(''); },
      stop: stopScan,
    };
  };
})();
