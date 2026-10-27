/* Diary page: renders the day from JSON and keeps it in sync after every change. */
(function () {
  const NT = window.NT;
  const D = window.DIARY;
  const MEALS = [['breakfast', '🌅'], ['lunch', '🍛'], ['dinner', '🌙'], ['snacks', '🍪']];
  const MACROS = [['protein', 'Protein', 'var(--series-1)'], ['carbs', 'Carbs', 'var(--series-2)'], ['fat', 'Fat', 'var(--series-3)']];
  let summary = D.summary;
  let activeCat = 'General';
  let freshIds = new Set();
  let editing = null;

  const targetsBy = () => Object.fromEntries(summary.targets.map((t) => [t.key, t]));

  // ---------- header ----------
  const hour = new Date().getHours();
  const hello = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
  document.getElementById('greeting').textContent = `${hello}${D.name ? ', ' + D.name : ''}`;
  document.getElementById('day-picker').addEventListener('change', (e) => { if (e.target.value) location.href = `/diary?d=${e.target.value}`; });
  if (D.day === NT.isoDate(new Date())) document.getElementById('today-btn').classList.add('hidden');

  // ---------- energy ring + macros ----------
  function renderSummary() {
    const t = targetsBy();
    const eaten = summary.totals.energy || 0;
    const goal = t.energy?.target || 0;
    const pct = goal ? eaten / goal : 0;
    const circle = document.querySelector('#energy-ring .value');
    const C = 2 * Math.PI * 52;
    circle.style.strokeDashoffset = String(C * (1 - Math.min(pct, 1)));
    circle.classList.toggle('over', pct > 1.1);
    document.getElementById('energy-ring').classList.toggle('done', pct >= 0.9 && pct <= 1.1);
    NT.countUp(document.getElementById('kcal-eaten'), eaten);
    const left = Math.round(goal - eaten);
    document.getElementById('kcal-left').textContent = goal ? (left >= 0 ? `${NT.fmt(left, 0)} kcal left` : `${NT.fmt(-left, 0)} kcal over`) : 'kcal';

    document.getElementById('macro-bars').innerHTML = MACROS.map(([k, label, color]) => {
      const v = summary.totals[k] || 0, g = t[k]?.target || 0;
      const w = g ? Math.min(100, (v / g) * 100) : 0;
      return `<div class="bar-row"><span class="name"><span class="swatch" style="background:${color}"></span>${label}</span>
        <span class="tabular small">${NT.fmt(v, 0)} / ${NT.fmt(g, 0)} g · ${summary.macro_split[k] || 0}% kcal</span>
        <div class="bar" role="progressbar" aria-label="${label}" aria-valuenow="${Math.round(w)}" aria-valuemin="0" aria-valuemax="100"><span style="width:${w}%;background:${color}"></span></div></div>`;
    }).join('') + `<div class="bar-row"><span class="name"><span class="swatch" style="background:var(--saffron)"></span>Fiber</span>
        <span class="tabular small">${NT.fmt(summary.totals.fiber, 0)} / ${NT.fmt(t.fiber?.target, 0)} g</span>
        <div class="bar"><span style="width:${t.fiber?.target ? Math.min(100, summary.totals.fiber / t.fiber.target * 100) : 0}%;background:var(--saffron)"></span></div></div>`;
  }

  // ---------- meals ----------
  function entryRow(e) {
    const n = e.nutrients || {};
    const qty = e.unit === 'g' || e.unit === 'ml' ? `${NT.fmt(e.grams, 0)} ${e.unit}` : `${NT.fmt(e.quantity)} × ${NT.esc(e.unit)} · ${NT.fmt(e.grams, 0)} g`;
    const src = e.source === 'photo' ? '<span class="badge" title="Logged from a photo">📸</span>' : e.source === 'text_ai' ? '<span class="badge" title="AI estimate">✨</span>' : '';
    return `<li class="entry ${freshIds.has(e.id) ? 'pop' : ''}" data-id="${e.id}">
      <span class="food-emoji" aria-hidden="true">${NT.emoji(e)}</span>
      <span class="title">${NT.esc(e.food_name)} ${src}</span>
      <span class="meta">${qty} · P ${NT.fmt(n.protein, 1)} · C ${NT.fmt(n.carbs, 1)} · F ${NT.fmt(n.fat, 1)}</span>
      <span class="kcal">${NT.fmt(n.energy, 0)}<div class="small muted">kcal</div></span>
      <span class="actions"><button class="icon-btn" data-edit="${e.id}" aria-label="Edit ${NT.esc(e.food_name)}">✏️</button>
      <button class="icon-btn" data-del="${e.id}" aria-label="Delete ${NT.esc(e.food_name)}">🗑️</button></span></li>`;
  }

  function renderMeals() {
    let count = 0;
    document.getElementById('meals').innerHTML = MEALS.map(([m, emo]) => {
      const meal = summary.meals[m];
      count += meal.entries.length;
      return `<div class="meal"><div class="meal-head"><h3><span aria-hidden="true">${emo}</span>${m}</h3>
        <span><span class="meal-kcal">${NT.fmt(meal.totals.energy, 0)} kcal</span>
        <button class="btn btn-sm btn-ghost" data-add-meal="${m}" aria-label="Add to ${m}">＋ Add</button></span></div>
        <ul class="entries">${meal.entries.map(entryRow).join('') || `<li class="empty">Nothing logged yet</li>`}</ul></div>`;
    }).join('');
    document.getElementById('entry-count').textContent = `${count} item${count === 1 ? '' : 's'}`;
  }

  // ---------- water ----------
  function renderWater() {
    const t = targetsBy();
    const total = summary.totals.water || 0;
    const goal = t.water?.target || 2500;
    const plain = summary.plain_water_ml || 0;
    const pct = Math.min(1, total / goal);
    document.getElementById('water-fill').style.height = `${Math.max(4, pct * 100)}%`;
    NT.countUp(document.getElementById('water-total'), total);
    document.getElementById('water-goal').textContent = `of ${NT.fmt(goal, 0)} ml`;
    document.getElementById('water-break').textContent = `${NT.fmt(plain, 0)} ml plain water · ${NT.fmt(Math.max(0, total - plain), 0)} ml from drinks & food`;
    document.getElementById('water-chip').textContent = pct >= 1 ? '🎉 Goal reached' : `${Math.round(pct * 100)}%`;
    document.getElementById('water-list').innerHTML = (summary.water_entries || []).map((e) =>
      `<li class="${freshIds.has(e.id) ? 'pop' : ''}" title="${new Date(e.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}">💧 ${NT.fmt(e.grams, 0)} ml
       <button data-del="${e.id}" aria-label="Remove ${NT.fmt(e.grams, 0)} ml water">✕</button></li>`).join('');
  }

  // ---------- nutrient targets ----------
  function renderTargets() {
    const cats = [...new Set(summary.targets.map((t) => t.category))];
    document.getElementById('target-tabs').innerHTML = cats.map((c) =>
      `<button class="tab ${c === activeCat ? 'active' : ''}" role="tab" aria-selected="${c === activeCat}" data-cat="${c}">${c}</button>`).join('');
    const label = { ok: '✓ On track', near: '◐ Almost there', low: '○ Low', over: '▲ Over limit', none: '' };
    document.getElementById('targets').innerHTML = summary.targets.filter((t) => t.category === activeCat).map((t) => {
      const w = t.pct === null ? 0 : Math.min(100, t.pct);
      const statusText = t.kind === 'max' && t.status === 'ok' ? '✓ Under limit' : label[t.status];
      return `<div class="target"><span class="label">${NT.esc(t.label)}${t.kind === 'max' ? ' <span class="badge">limit</span>' : ''}</span>
        <span class="nums">${NT.fmt(t.value)} / ${NT.fmt(t.target)} ${NT.esc(t.unit)}${t.pct !== null ? ` · ${Math.round(t.pct)}%` : ''}</span>
        <div class="bar"><span class="${t.status}" style="width:${w}%"></span></div>
        <span class="state">${statusText}</span></div>`;
    }).join('');
  }
  document.getElementById('target-tabs').addEventListener('click', (e) => {
    const c = e.target.closest('[data-cat]');
    if (c) { activeCat = c.dataset.cat; renderTargets(); }
  });

  function renderAll() {
    renderSummary(); renderMeals(); renderWater(); renderTargets();
    freshIds = new Set();
  }

  async function refresh(newEntries = []) {
    const before = targetsBy();
    newEntries.forEach((e) => freshIds.add(e.id));
    summary = await NT.api(`/api/diary?day=${D.day}`);
    renderAll();
    celebrate(before);
  }

  function celebrate(before) {
    const after = targetsBy();
    const reached = (k) => after[k] && after[k].pct >= (k === 'energy' ? 90 : 100) && !(before[k] && before[k].pct >= (k === 'energy' ? 90 : 100));
    if (reached('water')) { NT.confetti(['💧', '💦', '🎉', '✨']); NT.toast('Hydration goal reached! 💧'); }
    else if (reached('protein')) { NT.confetti(['💪', '🥚', '🫘', '🎉']); NT.toast('Protein goal reached! 💪'); }
    else if (reached('energy')) { NT.confetti(); NT.toast('Energy target reached for today 🎯'); }
  }

  document.addEventListener('nt:logged', (e) => refresh(e.detail).catch((err) => NT.toast(err.message)));

  // ---------- actions ----------
  document.addEventListener('click', async (e) => {
    const add = e.target.closest('[data-add-meal]');
    if (add) return NT.openAddFood(add.dataset.addMeal || null);

    const del = e.target.closest('[data-del]');
    if (del) {
      const row = del.closest('.entry, li');
      row?.classList.add('leaving');
      try {
        await NT.api(`/api/logs/${del.dataset.del}`, { method: 'DELETE' });
        await refresh();
      } catch (err) { row?.classList.remove('leaving'); NT.toast(err.message); }
      return;
    }

    const edit = e.target.closest('[data-edit]');
    if (edit) {
      editing = Object.values(summary.meals).flatMap((m) => m.entries).find((x) => x.id === edit.dataset.edit);
      if (!editing) return;
      document.getElementById('edit-name').textContent = editing.food_name;
      document.getElementById('edit-grams').value = Math.round(editing.grams);
      document.getElementById('edit-meal').value = editing.meal;
      document.getElementById('edit-dialog').showModal();
      return;
    }

    const water = e.target.closest('[data-water]');
    if (water) {
      NT.splash(e.clientX, e.clientY);
      try {
        const entry = await NT.api('/api/water', { method: 'POST', body: { logged_on: D.day, ml: Number(water.dataset.water) } });
        await refresh([entry]);
      } catch (err) { NT.toast(err.message); }
    }
  });

  document.getElementById('water-custom').addEventListener('click', async () => {
    const ml = Number(prompt('How much water (ml)?', '300'));
    if (!(ml > 0)) return;
    try {
      const entry = await NT.api('/api/water', { method: 'POST', body: { logged_on: D.day, ml } });
      await refresh([entry]);
    } catch (err) { NT.toast(err.message); }
  });

  document.getElementById('edit-save').addEventListener('click', async () => {
    if (!editing) return;
    const grams = Number(document.getElementById('edit-grams').value);
    const meal = document.getElementById('edit-meal').value;
    const body = { meal };
    if (grams > 0 && grams !== Number(editing.grams)) {
      body.grams = grams;
      body.quantity = Math.round((Number(editing.quantity) * grams / Number(editing.grams)) * 100) / 100 || 1;
    }
    try {
      await NT.api(`/api/logs/${editing.id}`, { method: 'PATCH', body });
      document.getElementById('edit-dialog').close();
      await refresh([editing]);
    } catch (err) { NT.toast(err.message); }
  });

  document.getElementById('copy-btn').addEventListener('click', async () => {
    const d = new Date(D.day + 'T00:00:00');
    d.setDate(d.getDate() - 1);
    try {
      const res = await NT.api('/api/logs/copy', { method: 'POST', body: { from_day: NT.isoDate(d), to_day: D.day } });
      NT.toast(res.copied ? `Copied ${res.copied} items from yesterday` : 'Nothing logged yesterday');
      if (res.copied) await refresh();
    } catch (err) { NT.toast(err.message); }
  });

  renderAll();
})();
