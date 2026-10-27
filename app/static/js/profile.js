/* Profile page: live health metrics, profile save, Gemini key, account security. */
(function () {
  const NT = window.NT;
  const form = document.getElementById('profile-form');
  const ACTIVITY = { sedentary: 1.2, light: 1.375, moderate: 1.55, active: 1.725, very_active: 1.9 };

  // ---------- live metrics ----------
  function metrics() {
    const v = (n) => form.elements[n].value;
    const h = Number(v('height_cm')), w = Number(v('weight_kg'));
    const dob = v('date_of_birth') ? new Date(v('date_of_birth')) : null;
    const age = dob ? Math.floor((Date.now() - dob) / 3.15576e10) : null;
    const cards = [];
    if (h && w) {
      const bmi = w / ((h / 100) ** 2);
      // Asian-Indian cut-offs (ICMR / WHO expert consultation): 18.5, 23, 25
      const [label, color] = bmi < 18.5 ? ['Underweight', 'var(--status-warn)'] : bmi < 23 ? ['Healthy', '#9ff0b6'] : bmi < 25 ? ['Overweight', 'var(--status-warn)'] : ['Obese', '#ffb4a3'];
      cards.push(['BMI', bmi.toFixed(1), `<span style="color:${color}">●</span> ${label} (Indian cut-offs)`]);
    } else cards.push(['BMI', '–', 'Add height & weight']);
    if (h && w && age) {
      const bmr = 10 * w + 6.25 * h - 5 * age + (v('sex') === 'female' ? -161 : 5);
      cards.push(['BMR', NT.fmt(bmr, 0), 'kcal at complete rest']);
      cards.push(['Daily burn', NT.fmt(bmr * (ACTIVITY[v('activity_level')] || 1.2), 0), 'kcal with your activity']);
    } else {
      cards.push(['BMR', '–', 'Add date of birth']);
      cards.push(['Daily burn', '–', 'kcal with your activity']);
    }
    cards.push(['Water need', w ? NT.fmt(w * 35 / 1000, 1) + ' L' : '–', '35 ml per kg body weight']);
    document.getElementById('metrics').innerHTML = cards.map(([k, val, s]) =>
      `<div class="macro-stat"><div class="k">${k}</div><div class="v">${val}</div><div class="tiny" style="opacity:.85;margin-top:2px">${s}</div></div>`).join('');
  }
  form.addEventListener('input', metrics);
  metrics();

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {};
    new FormData(form).forEach((v, k) => { body[k] = v === '' ? null : v; });
    ['height_cm', 'weight_kg'].forEach((k) => { if (body[k] !== null) body[k] = Number(body[k]); });
    if (!body.timezone) body.timezone = 'Asia/Kolkata';
    try {
      await NT.api('/api/profile', { method: 'PUT', body });
      if (document.getElementById('apply-goals').checked) {
        const s = await NT.api('/api/goals/suggested');
        await NT.api('/api/goals', { method: 'PUT', body: s });
        NT.toast(`Saved · ${NT.fmt(s.energy, 0)} kcal, ${NT.fmt(s.protein, 0)} g protein, ${NT.fmt(s.water / 1000, 1)} L water a day`, 4500);
        NT.confetti(['🎯', '🥗', '🌿', '✨']);
      } else NT.toast('Profile saved');
    } catch (err) { NT.toast(err.message); }
  });

  // ---------- Gemini key ----------
  const status = document.getElementById('ai-status');
  const removeBtn = document.getElementById('key-remove');
  function showStatus(s) {
    if (!s.byok_available) { status.className = 'alert alert-warn'; status.textContent = '⚠️ The server admin has not enabled storing personal API keys.'; }
    else if (s.has_user_key) { status.className = 'alert alert-info'; status.textContent = `✅ Using your Gemini key (${s.key_hint}).`; }
    else if (s.active_provider) { status.className = 'alert alert-info'; status.textContent = `Using the app's shared AI (${s.active_provider}). Add your own key for unlimited use.`; }
    else { status.className = 'alert alert-warn'; status.textContent = 'AI features are off - add your Gemini key to enable photo recognition.'; }
    removeBtn.classList.toggle('hidden', !s.has_user_key);
  }
  NT.api('/api/settings/ai').then(showStatus).catch((e) => { status.textContent = e.message; });
  document.getElementById('key-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = document.getElementById('gemini-key');
    const btn = e.submitter; btn.disabled = true; btn.textContent = 'Checking key…';
    try { showStatus(await NT.api('/api/settings/ai/gemini', { method: 'PUT', body: { api_key: input.value.trim() } })); input.value = ''; NT.toast('Gemini key saved 🔐'); }
    catch (err) { NT.toast(err.message, 4500); }
    finally { btn.disabled = false; btn.textContent = 'Save key'; }
  });
  removeBtn.addEventListener('click', async () => {
    if (!confirm('Remove your Gemini API key?')) return;
    try { showStatus(await NT.api('/api/settings/ai/gemini', { method: 'DELETE' })); NT.toast('Key removed'); }
    catch (err) { NT.toast(err.message); }
  });

  // ---------- account ----------
  document.getElementById('resend-verify')?.addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    try { await NT.api('/api/account/verify-email', { method: 'POST' }); NT.toast('Confirmation link sent 📬'); }
    catch (err) { NT.toast(err.message, 4000); e.currentTarget.disabled = false; }
  });
  document.getElementById('pw-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await NT.api('/api/account/password', { method: 'POST', body: { current_password: document.getElementById('cur-pw').value, new_password: document.getElementById('new-pw').value } });
      e.target.reset();
      NT.toast('Password changed 🔐');
    } catch (err) { NT.toast(err.message, 4000); }
  });
  document.getElementById('delete-account').addEventListener('click', async () => {
    const pw = prompt('This permanently deletes your account and all your data.\nEnter your password to confirm:');
    if (!pw) return;
    try {
      await NT.api('/api/account/delete', { method: 'POST', body: { password: pw } });
      location.href = '/login?msg=Your+account+has+been+deleted.';
    } catch (err) { NT.toast(err.message, 4000); }
  });
})();
