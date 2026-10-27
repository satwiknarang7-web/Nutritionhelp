/* "Add food" dialog: search, photo recognition, free-text estimate, custom food. */
(function () {
  const NT = window.NT;
  const dialog = document.getElementById('add-dialog');
  if (!dialog) return;
  const MEALS = ['breakfast', 'lunch', 'dinner', 'snacks'];
  let currentMeal = null;
  let photoFile = null;

  function guessMeal() {
    if (currentMeal) return currentMeal;
    const h = new Date().getHours();
    return h < 11 ? 'breakfast' : h < 16 ? 'lunch' : h < 19 ? 'snacks' : 'dinner';
  }

  function emitLogged(entries) {
    document.dispatchEvent(new CustomEvent('nt:logged', { detail: entries }));
  }

  // ---------- tabs ----------
  dialog.querySelectorAll('[data-tab]').forEach((tab) =>
    tab.addEventListener('click', () => showTab(tab.dataset.tab)));
  function showTab(name) {
    dialog.querySelectorAll('[data-tab]').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
    dialog.querySelectorAll('[data-panel]').forEach((p) => p.classList.toggle('hidden', p.dataset.panel !== name));
    if (name !== 'search') picker.stop();
    if (name === 'custom') buildCustomForm();
  }

  // ---------- search ----------
  const picker = NT.mountFoodPicker(document.getElementById('picker'), {
    submitLabel: 'Add to diary',
    showMeal: true,
    defaultMeal: guessMeal,
    onPick: async ({ food, grams, quantity, unit, meal }) => {
      const entry = await NT.api('/api/logs', {
        method: 'POST',
        body: {
          logged_on: window.DIARY.day, meal: meal || guessMeal(), food_ref: food.id, food_name: food.name,
          quantity, unit, grams, source: food.source === 'custom' ? 'custom' : 'manual',
        },
      });
      NT.toast(`Added ${food.name}`);
      emitLogged([entry]);
      dialog.close();
    },
  });

  NT.openAddFood = function (meal, tab = 'search', query = '') {
    currentMeal = meal || null;
    document.getElementById('add-meal-label').textContent = meal ? `· ${meal}` : '';
    const name = ['search', 'photo', 'describe', 'custom'].includes(tab) ? tab : 'search';
    picker.reset();
    showTab(name);
    if (!dialog.open) dialog.showModal();
    if (name === 'search') setTimeout(() => (query ? picker.search(query) : picker.focus()), 50);
    if (name === 'photo') setTimeout(() => document.getElementById('photo-input').click(), 80);
  };
  dialog.addEventListener('close', () => picker.stop());

  // ---------- AI review list (photo + describe) ----------
  function renderAiItems(container, result) {
    if (!result.is_food || !result.items.length) {
      container.innerHTML = `<div class="alert alert-info">No food detected. Try a clearer photo or describe it instead.</div>`;
      return;
    }
    const meal = guessMeal();
    container.innerHTML = `
      ${result.notes ? `<p class="small muted">💡 ${NT.esc(result.notes)}</p>` : ''}
      <div class="ai-items">${result.items.map((it, i) => `
        <div class="ai-item" data-i="${i}">
          <div class="ai-item-head">
            <input type="checkbox" checked aria-label="Include ${NT.esc(it.name)}">
            <span class="food-emoji">${NT.emoji(it)}</span>
            <span class="name">${NT.esc(it.name)}
              <span class="badge" title="${it.basis === 'database' ? 'Nutrition from the curated database' : 'Nutrition estimated by AI'}">${it.basis === 'database' ? '✓ database' : 'AI estimate'}</span>
              <span class="badge">${NT.esc(it.confidence)}</span></span>
            <strong class="tabular" data-kcal>${NT.fmt(it.nutrients.energy, 0)} kcal</strong>
          </div>
          <div class="small muted">${NT.fmt(it.quantity)} × ${NT.esc(it.unit)}</div>
          <div class="row">
            <div class="field" style="margin:0"><label>Amount (g / ml)</label><input type="number" min="1" step="1" value="${Math.round(it.grams)}" data-grams></div>
            <div class="field" style="margin:0"><label>Meal</label><select data-meal>${MEALS.map((m) => `<option ${m === meal ? 'selected' : ''} value="${m}">${m[0].toUpperCase() + m.slice(1)}</option>`).join('')}</select></div>
          </div>
          ${it.basis === 'ai_estimate' ? '<label class="small" style="display:flex;gap:6px;align-items:center;margin-top:8px;font-weight:500"><input type="checkbox" data-save style="width:auto;min-height:0"> Save to My foods for next time</label>' : ''}
        </div>`).join('')}</div>
      <button class="btn btn-primary btn-block" style="margin-top:12px" data-add-all>Add selected to diary</button>
      <div class="small muted" style="margin-top:6px;text-align:center">Analysed by ${NT.esc(result.provider || 'AI')} · always double-check portions</div>`;

    container.querySelectorAll('.ai-item').forEach((row) => {
      const it = result.items[Number(row.dataset.i)];
      row.querySelector('[data-grams]').addEventListener('input', (e) => {
        const g = Number(e.target.value) || 0;
        row.querySelector('[data-kcal]').textContent = `${NT.fmt((it.per_100g.energy || 0) * g / 100, 0)} kcal`;
      });
    });

    container.querySelector('[data-add-all]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      const entries = [];
      const saves = [];
      container.querySelectorAll('.ai-item').forEach((row) => {
        if (!row.querySelector('.ai-item-head input').checked) return;
        const it = result.items[Number(row.dataset.i)];
        const grams = Number(row.querySelector('[data-grams]').value) || it.grams;
        const scaleQty = it.grams ? grams / it.grams : 1;
        entries.push({
          logged_on: window.DIARY.day, meal: row.querySelector('[data-meal]').value,
          food_ref: it.food_ref, food_name: it.name, quantity: Math.round(it.quantity * scaleQty * 100) / 100 || 1,
          unit: it.unit, grams, source: result.source, per_100g: it.food_ref ? null : it.per_100g,
          photo_id: result.photo_id || null,
        });
        if (row.querySelector('[data-save]')?.checked) {
          saves.push({ name: it.name, serving_label: `1 ${it.unit}`, serving_grams: Math.max(1, it.grams / (it.quantity || 1)), per_100g: it.per_100g });
        }
      });
      if (!entries.length) return NT.toast('Select at least one item');
      btn.disabled = true;
      try {
        const created = await NT.api('/api/logs/batch', { method: 'POST', body: { entries } });
        await Promise.all(saves.map((s) => NT.api('/api/foods/custom', { method: 'POST', body: s }).catch(() => null)));
        NT.toast(`Added ${created.length} item${created.length > 1 ? 's' : ''}${saves.length ? ' · saved to My foods' : ''}`);
        emitLogged(created);
        dialog.close();
      } catch (err) { NT.toast(err.message, 4000); }
      finally { btn.disabled = false; }
    });
  }

  function thinking(container, label) {
    container.innerHTML = `<div class="thinking"><span class="spinner"></span> ${label}<span class="dots"><span>.</span><span>.</span><span>.</span></span></div>`;
  }

  function aiError(container, err) {
    const settingsHint = /api key|not set up|Settings/i.test(err.message) ? ' <a href="/profile#ai">Open settings</a>' : '';
    container.innerHTML = `<div class="alert alert-error">${NT.esc(err.message)}${settingsHint}</div>`;
  }

  // ---------- photo ----------
  const photoInput = document.getElementById('photo-input');
  const dropzone = document.getElementById('dropzone');
  const stage = document.getElementById('photo-stage');
  const photoResults = dialog.querySelector('[data-ai="photo"]');

  function setPhoto(file) {
    if (!file || !file.type.startsWith('image/')) return NT.toast('Please choose an image');
    photoFile = file;
    document.getElementById('photo-preview').src = URL.createObjectURL(file);
    dropzone.classList.add('hidden');
    stage.classList.remove('hidden');
    photoResults.innerHTML = '';
  }
  photoInput.addEventListener('change', () => setPhoto(photoInput.files[0]));
  ['dragover', 'dragenter'].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) => dropzone.addEventListener(ev, () => dropzone.classList.remove('drag')));
  dropzone.addEventListener('drop', (e) => { e.preventDefault(); setPhoto(e.dataTransfer.files[0]); });
  document.getElementById('photo-change').addEventListener('click', () => { photoInput.value = ''; photoInput.click(); });

  /** Downscale on the device before upload: faster on mobile data, same accuracy. */
  async function shrink(file, max = 1568) {
    try {
      const bmp = await createImageBitmap(file);
      const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(bmp.width * scale);
      canvas.height = Math.round(bmp.height * scale);
      canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
      return await new Promise((res) => canvas.toBlob((b) => res(b || file), 'image/jpeg', 0.85));
    } catch (_) { return file; }
  }

  document.getElementById('photo-analyze').addEventListener('click', async (e) => {
    if (!photoFile) return;
    const btn = e.currentTarget;
    btn.disabled = true;
    document.getElementById('scan-wrap').classList.add('scanning');
    thinking(photoResults, 'Recognising foods and portions');
    try {
      const form = new FormData();
      form.append('photo', await shrink(photoFile), 'meal.jpg');
      form.append('note', document.getElementById('photo-note').value);
      const result = await NT.api('/api/analyze/photo', { method: 'POST', form });
      renderAiItems(photoResults, { ...result, source: 'photo' });
    } catch (err) { aiError(photoResults, err); }
    finally { btn.disabled = false; document.getElementById('scan-wrap').classList.remove('scanning'); }
  });

  // ---------- describe ----------
  const describeResults = dialog.querySelector('[data-ai="describe"]');
  document.getElementById('describe-go').addEventListener('click', async (e) => {
    const text = document.getElementById('describe-text').value.trim();
    if (text.length < 2) return NT.toast('Describe what you ate');
    const btn = e.currentTarget;
    btn.disabled = true;
    thinking(describeResults, 'Estimating nutrition');
    try {
      const result = await NT.api('/api/analyze/text', { method: 'POST', body: { description: text } });
      renderAiItems(describeResults, { ...result, source: 'text_ai' });
    } catch (err) { aiError(describeResults, err); }
    finally { btn.disabled = false; }
  });

  // ---------- custom food ----------
  let nutrientList = null;
  async function buildCustomForm() {
    const box = document.getElementById('cf-nutrients');
    if (box.childElementCount) return;
    nutrientList = nutrientList || await NT.api('/api/nutrients');
    box.innerHTML = nutrientList.filter((n) => n.key !== 'water').map((n) => `
      <div class="goal-input"><label for="cf-${n.key}">${NT.esc(n.label)} <span class="unit">${NT.esc(n.unit)}</span></label>
      <input id="cf-${n.key}" data-key="${n.key}" type="number" min="0" step="any" inputmode="decimal"></div>`).join('');
  }
  document.getElementById('custom-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const nutrients = {};
    document.querySelectorAll('#cf-nutrients input').forEach((i) => { if (i.value !== '') nutrients[i.dataset.key] = Number(i.value); });
    if (nutrients.energy === undefined) return NT.toast('Energy (kcal) is required');
    const grams = Number(document.getElementById('cf-grams').value);
    const serving = document.getElementById('cf-serving').value || '1 serving';
    try {
      const food = await NT.api('/api/foods/custom', {
        method: 'POST',
        body: { name: document.getElementById('cf-name').value, serving_label: serving, serving_grams: grams, nutrients_per_serving: nutrients },
      });
      const entry = await NT.api('/api/logs', {
        method: 'POST',
        body: { logged_on: window.DIARY.day, meal: guessMeal(), food_ref: food.id, food_name: food.name, quantity: 1, unit: serving, grams, source: 'custom' },
      });
      NT.toast(`Saved ${food.name}`);
      e.target.reset();
      emitLogged([entry]);
      dialog.close();
    } catch (err) { NT.toast(err.message, 4000); }
  });
})();
