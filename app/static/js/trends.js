/* Trends page: day / week / month / year / custom reports with Chart.js. */
(function () {
  const NT = window.NT;
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  let period = 'week';
  let data = null;
  const charts = {};

  const tabs = document.getElementById('period-tabs');
  tabs.addEventListener('click', (e) => {
    const t = e.target.closest('[data-period]');
    if (!t) return;
    period = t.dataset.period;
    tabs.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === t));
    document.querySelectorAll('[data-custom]').forEach((el) => el.classList.toggle('hidden', period !== 'custom'));
    document.getElementById('anchor-wrap').classList.toggle('hidden', period === 'custom');
    if (period === 'custom' && !document.getElementById('start').value) {
      const d = new Date(); d.setDate(d.getDate() - 13);
      document.getElementById('start').value = NT.isoDate(d);
    }
    load();
  });
  ['anchor', 'start', 'end'].forEach((id) => document.getElementById(id).addEventListener('change', load));
  document.getElementById('nutrient-select').addEventListener('change', drawNutrient);

  async function load() {
    const p = new URLSearchParams({ period });
    if (period === 'custom') {
      p.set('start', document.getElementById('start').value);
      p.set('end', document.getElementById('end').value);
    } else p.set('anchor', document.getElementById('anchor').value);
    try {
      data = await NT.api(`/api/stats?${p}`);
      render();
    } catch (err) { NT.toast(err.message); }
  }

  function fmtDate(iso, g) {
    const d = new Date(iso + 'T00:00:00');
    if (g === 'month') return d.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' });
    if (g === 'week') return 'Wk of ' + d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  }

  function baseOptions(unit) {
    const grid = css('--border'), ink = css('--text-2');
    return {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 700, easing: 'easeOutCubic' },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: ink, usePointStyle: true, boxWidth: 8, boxHeight: 8 } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${NT.fmt(c.parsed.y)} ${unit}` } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: ink, maxRotation: 0, autoSkipPadding: 12 } },
        y: { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { color: ink } },
      },
    };
  }

  function chart(id, config) {
    charts[id]?.destroy();
    charts[id] = new window.Chart(document.getElementById(id), config);
  }

  function goalLine(label, value, n) {
    return {
      type: 'line', label, data: Array(n).fill(value), borderColor: css('--text-3'), borderDash: [6, 5],
      borderWidth: 2, pointRadius: 0, fill: false, order: 0,
    };
  }

  function barDataset(label, values, color) {
    return { type: 'bar', label, data: values, backgroundColor: color, borderRadius: 4, borderSkipped: 'start', maxBarThickness: 28, order: 1 };
  }

  function render() {
    const empty = data.logged_days === 0;
    document.getElementById('empty-state').classList.toggle('hidden', !empty);
    document.getElementById('report').classList.toggle('hidden', empty);
    const s = new Date(data.start + 'T00:00:00'), e = new Date(data.end + 'T00:00:00');
    const opt = { day: 'numeric', month: 'short', year: 'numeric' };
    document.getElementById('range-label').textContent = data.start === data.end
      ? s.toLocaleDateString('en-IN', opt) : `${s.toLocaleDateString('en-IN', opt)} – ${e.toLocaleDateString('en-IN', opt)} · ${data.days} days`;
    if (empty) return;

    const g = data.goals, avg = data.averages;
    NT.countUp(document.getElementById('k-energy'), avg.energy);
    document.getElementById('k-energy-sub').textContent = `goal ${NT.fmt(g.energy, 0)} kcal`;
    NT.countUp(document.getElementById('k-protein'), avg.protein, 1);
    document.getElementById('k-protein-sub').textContent = `goal ${NT.fmt(g.protein, 0)} g · met ${data.days_protein_met}×`;
    document.getElementById('k-days').textContent = `${data.logged_days}/${data.days}`;
    document.getElementById('k-days-sub').textContent = `${Math.round(data.logged_days / data.days * 100)}% consistency`;
    document.getElementById('k-target').textContent = `${data.days_on_energy_target} day${data.days_on_energy_target === 1 ? '' : 's'}`;
    NT.countUp(document.getElementById('k-water'), avg.water);
    document.getElementById('k-water-sub').textContent = `ml · goal ${NT.fmt(g.water, 0)} ml`;

    const labels = data.series.map((p) => fmtDate(p.date, data.granularity));
    const n = labels.length;
    document.getElementById('energy-note').textContent = data.granularity === 'day' ? 'per day' : `daily average per ${data.granularity}`;

    chart('c-energy', {
      data: { labels, datasets: [barDataset('Energy', data.series.map((p) => p.energy), css('--series-1')), goalLine('Goal', g.energy, n)] },
      options: baseOptions('kcal'),
    });

    const macroOpts = baseOptions('kcal');
    macroOpts.scales.x.stacked = true;
    macroOpts.scales.y.stacked = true;
    chart('c-macros', {
      type: 'bar',
      data: {
        labels,
        datasets: [
          ['Protein', 'protein', 4, '--series-1'], ['Carbs', 'carbs', 4, '--series-2'], ['Fat', 'fat', 9, '--series-3'],
        ].map(([label, k, f, c]) => ({
          label, data: data.series.map((p) => Math.round(p[k] * f)), backgroundColor: css(c),
          borderColor: css('--surface'), borderWidth: { top: 2 }, borderSkipped: 'start', maxBarThickness: 28,
        })),
      },
      options: macroOpts,
    });

    chart('c-water', {
      data: { labels, datasets: [barDataset('Water', data.series.map((p) => p.water), css('--water')), goalLine('Goal', g.water, n)] },
      options: baseOptions('ml'),
    });

    const doughnut = (id, labelsArr, values, colors, unit) => chart(id, {
      type: 'doughnut',
      data: { labels: labelsArr, datasets: [{ data: values, backgroundColor: colors, borderColor: css('--surface'), borderWidth: 2 }] },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '62%',
        animation: { animateRotate: true, duration: 900 },
        plugins: {
          legend: { position: 'bottom', labels: { color: css('--text-2'), usePointStyle: true, boxWidth: 8, generateLabels: (c) => c.data.labels.map((l, i) => ({ text: `${l} ${NT.fmt(c.data.datasets[0].data[i], 0)}%`, fillStyle: colors[i], strokeStyle: colors[i], pointStyle: 'circle', index: i, fontColor: css('--text-2') })) } },
          tooltip: { callbacks: { label: (c) => `${c.label}: ${NT.fmt(c.parsed, 0)}${unit}` } },
        },
      },
    });
    doughnut('c-split', ['Protein', 'Carbs', 'Fat'], [data.macro_split.protein, data.macro_split.carbs, data.macro_split.fat],
      [css('--series-1'), css('--series-2'), css('--series-3')], '%');
    doughnut('c-meals', ['Breakfast', 'Lunch', 'Dinner', 'Snacks'],
      ['breakfast', 'lunch', 'dinner', 'snacks'].map((m) => data.meal_split[m]),
      [css('--series-1'), css('--series-2'), css('--series-3'), css('--series-4')], '%');

    drawNutrient();

    document.getElementById('top-foods').innerHTML = data.top_foods.map((f) => `<tr>
      <td><span aria-hidden="true">${NT.emoji(f)}</span> ${NT.esc(f.name)}</td><td class="num">${f.count}</td>
      <td class="num">${NT.fmt(f.energy, 0)}</td><td class="num">${NT.fmt(f.protein, 1)}</td></tr>`).join('');

    const statusLabel = { ok: '✓ On track', near: '◐ Close', low: '○ Low', over: '▲ Over', none: '–' };
    let lastCat = '';
    document.getElementById('nutrient-table').innerHTML = data.targets.map((t) => {
      const head = t.category !== lastCat ? `<tr><th colspan="5" style="padding-top:14px">${t.category}</th></tr>` : '';
      lastCat = t.category;
      return `${head}<tr><td>${NT.esc(t.label)}${t.kind === 'max' ? ' <span class="badge">limit</span>' : ''}</td>
        <td class="num">${NT.fmt(t.value)} ${NT.esc(t.unit)}</td><td class="num hide-sm">${NT.fmt(t.target)}</td>
        <td class="num">${t.pct === null ? '–' : Math.round(t.pct) + '%'}</td>
        <td><span class="small">${t.kind === 'max' && t.status === 'ok' ? '✓ Under limit' : statusLabel[t.status]}</span></td></tr>`;
    }).join('');
  }

  function drawNutrient() {
    if (!data || data.logged_days === 0) return;
    const sel = document.getElementById('nutrient-select');
    const key = sel.value;
    const meta = data.targets.find((t) => t.key === key);
    const labels = data.series.map((p) => fmtDate(p.date, data.granularity));
    const datasets = [barDataset(meta.label, data.series.map((p) => p[key]), css('--series-3'))];
    if (meta.target) datasets.push(goalLine(meta.kind === 'max' ? 'Limit' : 'Goal', meta.target, labels.length));
    chart('c-nutrient', { data: { labels, datasets }, options: baseOptions(meta.unit) });
  }

  load();
})();
