/* Whale Pool frontend — vanilla JS SPA */
const app = document.getElementById('app');
const navLinks = document.getElementById('navLinks');
const modeBadge = document.getElementById('modeBadge');

const store = {
  get token() { return localStorage.getItem('wp_token'); },
  set token(v) { v ? localStorage.setItem('wp_token', v) : localStorage.removeItem('wp_token'); },
  user: null,
};

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function money(cents) {
  return '$' + (cents / 100).toLocaleString('en-US', { maximumFractionDigits: 0 });
}
function money2(cents) {
  return '$' + (cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

async function api(path, opts = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
  if (store.token) headers['Authorization'] = 'Bearer ' + store.token;
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-json */ }
  if (res.status === 401 && store.token) { store.token = null; store.user = null; location.hash = '#/login'; }
  return { ok: res.ok, status: res.status, data };
}

async function refreshMe() {
  if (!store.token) { store.user = null; return null; }
  const r = await api('/api/me');
  store.user = r.ok ? r.data : null;
  if (!r.ok) store.token = null;
  return store.user;
}

function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const [pathPart, queryPart] = h.split('?');
  const segs = pathPart.split('/').filter(Boolean);
  const query = {};
  (queryPart || '').split('&').forEach(p => {
    const [k, v] = p.split('=');
    if (k) query[decodeURIComponent(k)] = decodeURIComponent(v || '');
  });
  return { segs, query };
}

function setModeBadge(mode) {
  if (!mode) return;
  const live = mode === 'live';
  modeBadge.innerHTML = `<span class="${live ? 'mode-live' : 'mode-test'}">${live ? '● LIVE' : '● TEST MODE — no real charges'}</span>`;
}

function renderNav() {
  const u = store.user;
  let html = `<a href="#/projects">Projects</a>`;
  if (u) {
    html += `<a href="#/dashboard">Dashboard</a><a href="#/submit">Submit project</a>`;
    if (u.is_admin) html += `<a href="#/admin">Admin</a>`;
    html += `<a href="#/" id="logoutLink">Log out</a>`;
  } else {
    html += `<a href="#/login">Log in</a><a href="#/join" class="cta">Join — $30/mo</a>`;
  }
  navLinks.innerHTML = html;
  const lo = document.getElementById('logoutLink');
  if (lo) lo.addEventListener('click', e => { e.preventDefault(); store.token = null; store.user = null; location.hash = '#/'; });
}

/* ---------------- landing ---------------- */
async function renderLanding() {
  const r = await api('/api/stats');
  const s = r.ok ? r.data : { members: 0, active_members: 0, total_pledged_cents: 0, projects_funded: 0 };
  setModeBadge(s.mode);
  app.innerHTML = `
  <div class="hero">
    <span class="kicker">COMMUNITY FUND · WEEKLY ROUNDS</span>
    <h1>Don't pitch sharks.<br><span class="grad">Swim with whales.</span></h1>
    <p class="lead">Whale Pool is the member-funded launchpad where everyday builders back each other's
    boldest ideas — reviewed by the community, refined together, funded together.</p>
    <div class="hero-ctas">
      <a class="btn" href="#/join">Join the pool — $30/month</a>
      <a class="btn ghost" href="#/projects">Browse this week's projects</a>
    </div>
  </div>
  <div class="stats">
    <div class="stat"><div class="n">${s.members}</div><div class="l">MEMBERS</div></div>
    <div class="stat"><div class="n">${s.active_members}</div><div class="l">ACTIVE</div></div>
    <div class="stat"><div class="n">${money(s.total_pledged_cents)}</div><div class="l">PLEDGED</div></div>
    <div class="stat"><div class="n">${s.projects_funded}</div><div class="l">PROJECTS FUNDED</div></div>
  </div>
  <div class="section">
    <h2>How the pool works</h2>
    <p class="sub">Four simple moves. No gatekeepers — the community is the gate.</p>
    <div class="grid4">
      <div class="card"><div class="step">01</div><h3>Join for $30/month</h3><p>Your monthly membership keeps the pool alive and unlocks funding, referrals and voting on every project.</p></div>
      <div class="card"><div class="step">02</div><h3>Refer &amp; earn $10</h3><p>Share your referral link. Every member who joins through it credits your account $10 — automatically tracked.</p></div>
      <div class="card"><div class="step">03</div><h3>Unlock a loan up to $10,000</h3><p>Reach 10 referrals ($100 in credits) and you can bring a project to the pool for a loan of up to $10,000 — half paid to you, half held in the reserve.</p></div>
      <div class="card"><div class="step">04</div><h3>Fund each other</h3><p>Every week new projects go live. It takes 100 member backers to fund one — every backer becomes a part-owner, and 50% of the loan sits in an income-generating reserve you recoup after buying back 100% of shares.</p></div>
    </div>
  </div>
  <div class="section">
    <h2>The math is the pitch</h2>
    <p class="sub">Transparent by design. Everyone sees the same numbers.</p>
    <div class="math">
      <div class="math-row"><span>Monthly membership</span><b>$30</b></div>
      <div class="math-row"><span>Referral credit, per joined member</span><b>$10</b></div>
      <div class="math-row"><span>10 referrals = credits</span><b>$100</b></div>
      <div class="math-row"><span>Max loan (credits × 100)</span><b>$10,000</b></div>
      <div class="math-row"><span>Paid to you at funding</span><b>50%</b></div>
      <div class="math-row"><span>Held in income-generating reserve</span><b>50%</b></div>
      <div class="math-row"><span>Member backers required to fund a project</span><b>100</b></div>
      <div class="math-row"><span>Ownership per backer — hard-capped</span><b>max 1%</b></div>
      <div class="math-row"><span>Founder buyback · or annual dividend</span><b>cost + 10%</b></div>
      <div class="math-row"><span>New project rounds</span><b>Every week</b></div>
    </div>
    <div style="text-align:center;margin-top:30px"><a class="btn" href="#/join">Claim your spot</a></div>
  </div>`;
}

/* ---------------- auth ---------------- */
function renderJoin(query) {
  const ref = esc(query.ref || '');
  app.innerHTML = `
  <div class="form-card">
    <h2>Join Whale Pool</h2>
    <p class="sub">$30/month membership · earn $10 per referral · unlock loans up to $10,000</p>
    <div id="msg"></div>
    <form id="f">
      <label>Full name</label><input name="name" required autocomplete="name">
      <label>Email</label><input name="email" type="email" required autocomplete="email">
      <label>Password (8+ characters)</label><input name="password" type="password" required minlength="8" autocomplete="new-password">
      <label>Referral code (optional)</label><input name="referral_code" value="${ref}" placeholder="e.g. A1B2C3D4" style="text-transform:uppercase">
      <button class="btn" type="submit">Create account</button>
    </form>
    <p class="hint">Already a member? <a href="#/login">Log in</a></p>
  </div>`;
  document.getElementById('f').addEventListener('submit', async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const r = await api('/api/auth/register', { method: 'POST', body: JSON.stringify({
      name: fd.get('name'), email: fd.get('email'), password: fd.get('password'),
      referral_code: fd.get('referral_code'),
    })});
    if (r.ok) { store.token = r.data.token; store.user = r.data.user; location.hash = '#/dashboard'; }
    else document.getElementById('msg').innerHTML = `<div class="err">${esc(r.data.error || 'Something went wrong')}</div>`;
  });
}

function renderLogin() {
  app.innerHTML = `
  <div class="form-card">
    <h2>Welcome back</h2>
    <p class="sub">Log in to your Whale Pool account</p>
    <div id="msg"></div>
    <form id="f">
      <label>Email</label><input name="email" type="email" required autocomplete="email">
      <label>Password</label><input name="password" type="password" required autocomplete="current-password">
      <button class="btn" type="submit">Log in</button>
    </form>
    <p class="hint">New here? <a href="#/join">Join the pool</a></p>
  </div>`;
  document.getElementById('f').addEventListener('submit', async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const r = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({
      email: fd.get('email'), password: fd.get('password'),
    })});
    if (r.ok) { store.token = r.data.token; store.user = r.data.user; location.hash = '#/dashboard'; }
    else document.getElementById('msg').innerHTML = `<div class="err">${esc(r.data.error || 'Something went wrong')}</div>`;
  });
}

/* ---------------- dashboard ---------------- */
async function renderDashboard(query) {
  const u = await refreshMe();
  if (!u) { location.hash = '#/login'; return; }
  renderNav();
  const b = await api('/api/billing/status');
  const bill = b.ok ? b.data : { mode: 'test', membership_status: u.membership_status };
  setModeBadge(bill.mode);
  const e = u.eligibility, rs = u.referral_stats;
  const pct = Math.min(100, Math.round(rs.credited / 10 * 100));
  const paid = query.paid, canceled = query.canceled;

  app.innerHTML = `
  <div class="dash">
    <h2>Hey ${esc(u.name.split(' ')[0])} 🐋</h2>
    ${paid ? '<div class="okmsg">Payment received — welcome to the pool! Your membership is active.</div>' : ''}
    ${canceled ? '<div class="err">Checkout was canceled. No charge was made.</div>' : ''}
    <div class="cards">
      <div class="panel">
        <h3>MEMBERSHIP</h3>
        <div class="rowflex">
          <span class="pill ${u.membership_status}">${esc(u.membership_status.toUpperCase())}</span>
          ${u.membership_period_end ? `<span style="color:var(--mut);font-size:13px">renews ${esc(u.membership_period_end.slice(0,10))}</span>` : ''}
        </div>
        <p style="color:var(--mut);font-size:14px;margin:12px 0">$30/month keeps your funding, referral and pledge powers switched on.</p>
        ${u.membership_status !== 'active' ? (
          bill.mode === 'test'
            ? `<button class="btn small" id="testPay">Test pay $30 (no real charge)</button>`
            : `<button class="btn small" id="checkout">Pay $30/month with Stripe</button>`
        ) : `<button class="btn small ghost" id="manageBtn" disabled>Managed via Stripe</button>`}
      </div>
      <div class="panel">
        <h3>YOUR REFERRAL LINK</h3>
        <div class="bignum">${money(u.referral_credits_cents)}</div>
        <div style="color:var(--mut);font-size:13px">referral credits · $10 per joined member</div>
        <div class="refbox"><input id="refLink" readonly value="${esc(u.referral_link)}"><button class="btn small" id="copyRef">Copy</button></div>
        <div class="progress"><i style="width:${pct}%"></i></div>
        <div style="font-size:13px;color:var(--mut)">${rs.credited} of 10 credited referrals ${rs.total > rs.credited ? `(${rs.total - rs.credited} pending first payment)` : ''}</div>
      </div>
      <div class="panel">
        <h3>FUNDING ELIGIBILITY</h3>
        ${e.can_submit
          ? `<div class="okmsg" style="margin:0">You're eligible — you can request up to <b>${money(e.max_goal_cents)}</b>.</div>
             <div style="margin-top:12px"><a class="btn small warn" href="#/submit">Submit a project</a></div>`
          : `<ul style="color:var(--mut);font-size:14px;padding-left:18px;display:grid;gap:6px">
               <li>${e.membership_active ? '✅' : '⬜'} Active membership ${e.membership_active ? '' : '— pay $30/month above'}</li>
               <li>${e.referrals_credited >= 10 ? '✅' : '⬜'} ${e.referrals_credited}/10 credited referrals</li>
             </ul>
             <p style="color:var(--mut);font-size:13px;margin-top:10px">Max ask right now: <b style="color:var(--gold)">${money(e.max_goal_cents)}</b> (credits × 100)</p>`}
      </div>
    </div>
    <div class="panel">
      <h3>MY PROJECTS</h3>
      <div id="myProjs"><span style="color:var(--mut)">Loading…</span></div>
    </div>
  </div>`;

  const tp = document.getElementById('testPay');
  if (tp) tp.addEventListener('click', async () => {
    tp.disabled = true; tp.textContent = 'Processing…';
    const r = await api('/api/billing/test-pay', { method: 'POST' });
    location.hash = '#/dashboard'; navigate();
  });
  const co = document.getElementById('checkout');
  if (co) co.addEventListener('click', async () => {
    co.disabled = true; co.textContent = 'Redirecting…';
    const r = await api('/api/billing/checkout', { method: 'POST' });
    if (r.ok) location.href = r.data.url;
    else { co.disabled = false; co.textContent = 'Pay $30/month with Stripe'; alert(r.data.message || r.data.error); }
  });
  document.getElementById('copyRef').addEventListener('click', () => {
    const i = document.getElementById('refLink'); i.select();
    document.execCommand('copy');
    if (navigator.clipboard) navigator.clipboard.writeText(i.value).catch(() => {});
    document.getElementById('copyRef').textContent = 'Copied!';
    setTimeout(() => document.getElementById('copyRef').textContent = 'Copy', 1500);
  });
  const mp = await api('/api/me/projects');
  document.getElementById('myProjs').innerHTML = mp.ok && mp.data.length
    ? mp.data.map(p => `
      <div class="rowflex" style="padding:10px 0;border-bottom:1px solid var(--line)">
        <span><b>${esc(p.title)}</b> <span class="status-pill st-${p.status}">${esc(p.status.replace('_',' ').toUpperCase())}</span><br>
        <span style="color:var(--mut);font-size:13px">${money(p.pledged_cents)} of ${money(p.goal_cents)} · ${p.backer_count} backers</span></span>
        <a class="btn small ghost" href="#/project/${p.id}">View</a>
      </div>`).join('')
    : '<span style="color:var(--mut)">No projects yet. Hit 10 referrals and bring your first idea to the pool.</span>';
}

/* ---------------- projects ---------------- */
function projCard(p) {
  const pct = p.goal_cents ? Math.min(100, Math.round(p.pledged_cents / p.goal_cents * 100)) : 0;
  return `
  <a class="proj" href="#/project/${p.id}" style="color:inherit">
    <span class="status-pill st-${p.status}">${esc(p.status.replace('_',' ').toUpperCase())}</span>
    <span class="cat">${esc((p.category || 'general').toUpperCase())} · by ${esc(p.owner_name)}</span>
    <h3>${esc(p.title)}</h3>
    <div class="tag">${esc(p.tagline)}</div>
    <div class="bar"><i style="width:${pct}%"></i></div>
    <div class="meta"><span><b>${money(p.pledged_cents)}</b> pledged</span><span>goal <b>${money(p.goal_cents)}</b></span><span><b>${p.backer_count}</b>/${p.min_backers || 100} backers</span></div>
  </a>`;
}

async function renderProjects() {
  const r = await api('/api/rounds/current');
  const round = r.ok ? r.data : { week_start: '', projects: [] };
  app.innerHTML = `
  <div class="dash">
    <h2>This week's funding round</h2>
    <p class="sub" style="color:var(--mut);margin-bottom:20px">Week of ${esc(round.week_start)} · ${round.projects.length} project${round.projects.length === 1 ? '' : 's'} live</p>
    ${round.projects.length ? `<div class="proj-grid">${round.projects.map(projCard).join('')}</div>`
      : `<div class="panel"><p style="color:var(--mut)">No projects live yet this week. Check back soon — or <a href="#/submit">bring the first one</a>.</p></div>`}
  </div>`;
}

async function renderProjectDetail(id, query) {
  const r = await api('/api/projects/' + id);
  if (!r.ok) { app.innerHTML = '<div class="dash"><div class="err">Project not found.</div></div>'; return; }
  const p = r.data;
  const pct = p.goal_cents ? Math.min(100, Math.round(p.pledged_cents / p.goal_cents * 100)) : 0;
  const u = store.user;
  app.innerHTML = `
  <div class="detail">
    ${query.pledged ? '<div class="okmsg">Pledge received — thank you for backing this builder! 🐋</div>' : ''}
    <div class="panel">
      <span class="status-pill st-${p.status}">${esc(p.status.replace('_',' ').toUpperCase())}</span>
      <div class="cat" style="margin-top:10px;font-size:11px;font-weight:800;letter-spacing:.16em;color:var(--cyan)">${esc((p.category || 'general').toUpperCase())} · by ${esc(p.owner_name)}</div>
      <h2 style="font-size:30px;margin:8px 0">${esc(p.title)}</h2>
      <p style="color:var(--mut);font-size:17px;margin-bottom:14px">${esc(p.tagline)}</p>
      <p style="white-space:pre-wrap">${esc(p.description)}</p>
    </div>
    <div class="panel">
      <h3>FUNDING PROGRESS</h3>
      <div class="bignum">${money(p.pledged_cents)} <span style="font-size:18px;color:var(--mut)">of ${money(p.goal_cents)}</span></div>
      <div class="progress"><i style="width:${pct}%"></i></div>
      <div style="color:var(--mut);font-size:13px">${pct}% · ${p.backer_count} of ${p.min_backers || 100} member backers</div>
      ${p.status === 'live' && p.backers_needed > 0 ? `<div style="color:var(--gold);font-size:13px;margin-top:6px">Needs <b>${p.backers_needed}</b> more member backer${p.backers_needed === 1 ? '' : 's'} to unlock funding — every backer becomes a part-owner.</div>` : ''}
      ${p.status === 'live' ? `
        <div id="pledgeMsg" style="margin-top:10px"></div>
        ${u ? `
        <div class="pledge-row">
          <input id="pledgeAmt" type="number" min="1" step="1" placeholder="$ amount">
          <button class="btn small warn" id="pledgeBtn">Back this project</button>
        </div>
        <p class="hint" style="text-align:left">Active membership required to pledge.</p>` : `
        <p style="margin-top:12px"><a class="btn small" href="#/join">Join to back this project</a></p>`}
      ` : p.status === 'funded' ? `<div class="okmsg" style="margin-top:12px">🎉 Fully funded by the pool!</div>` : ''}
    </div>
    <div id="equityPanel"></div>
    <div id="reservePanel"></div>
    <p><a href="#/projects">← Back to this week's round</a></p>
  </div>`;
  renderEquityPanel(p);
  renderReservePanel(p);
  const btn = document.getElementById('pledgeBtn');
  if (btn) btn.addEventListener('click', async () => {
    const amt = parseFloat(document.getElementById('pledgeAmt').value);
    if (!amt || amt < 1) { document.getElementById('pledgeMsg').innerHTML = '<div class="err">Enter at least $1.</div>'; return; }
    btn.disabled = true; btn.textContent = 'Processing…';
    const mode = (await api('/api/billing/status')).data.mode;
    if (mode === 'test') {
      const pr = await api(`/api/projects/${id}/test-pledge`, { method: 'POST', body: JSON.stringify({ amount_dollars: amt }) });
      if (pr.ok) { location.hash = `#/project/${id}?pledged=1`; navigate(); }
      else { document.getElementById('pledgeMsg').innerHTML = `<div class="err">${esc(pr.data.error || 'Failed')}</div>`; btn.disabled = false; btn.textContent = 'Back this project'; }
    } else {
      const pr = await api(`/api/projects/${id}/pledge`, { method: 'POST', body: JSON.stringify({ amount_dollars: amt }) });
      if (pr.ok) location.href = pr.data.url;
      else { document.getElementById('pledgeMsg').innerHTML = `<div class="err">${esc(pr.data.error || 'Failed')}</div>`; btn.disabled = false; btn.textContent = 'Back this project'; }
    }
  });
}

/* ---------------- reserve ---------------- */
async function renderReservePanel(p) {
  const el = document.getElementById('reservePanel');
  if (!el || p.status !== 'funded') return;
  const r = await api(`/api/projects/${p.id}/reserve`);
  if (!r.ok) return;
  const v = r.data;
  const isOwner = store.user && store.user.id === p.owner_id;
  let html = `<div class="panel"><h3>LOAN &amp; RESERVE</h3>
    <p style="color:var(--mut);font-size:14px;margin-bottom:12px">Funded as a <b>${money(v.loan_cents)} loan</b> to the founder.
    <b style="color:var(--cyan)">${money(v.disbursed_cents)}</b> went to the founder and
    <b style="color:var(--gold)">${money(v.reserve_cents)} (50%)</b> is held in the platform's
    income-generating reserve to offset future obligations.</p>
    <div class="math-row"><span>Reserve balance</span><b>${money(v.reserve_cents)}</b></div>
    <div class="math-row"><span>Yield accrued (${v.days_in_reserve} days)</span><b>${money(v.accrued_yield_cents)}</b></div>`;
  if (v.reserve_released) {
    html += `<div class="okmsg" style="margin-top:12px">Reserve deposit recouped by the founder${v.reserve_released_at ? ' on ' + esc(v.reserve_released_at.slice(0, 10)) : ''}.</div>`;
  } else if (v.reclaimable && isOwner) {
    html += `<div class="decide" style="margin-top:14px">
        <button class="btn small warn" id="reclaimBtn">Reclaim ${money(v.reserve_cents + v.accrued_yield_cents)} deposit</button>
      </div><div id="rsvMsg" style="margin-top:10px"></div>
      <p class="hint" style="text-align:left">Available because you bought back 100% of member shares.</p>`;
  } else if (!v.buyback_complete) {
    html += `<p class="hint" style="text-align:left;margin-top:10px">The founder recoups this deposit after buying back 100% of member shares.</p>`;
  }
  html += `</div>`;
  el.innerHTML = html;
  const btn = document.getElementById('reclaimBtn');
  if (btn) btn.addEventListener('click', async () => {
    btn.disabled = true; btn.textContent = 'Processing…';
    const rr = await api(`/api/projects/${p.id}/reclaim-deposit`, { method: 'POST' });
    const msg = document.getElementById('rsvMsg');
    if (rr.ok) { location.reload(); }
    else { msg.innerHTML = `<div class="err">${esc(rr.data.error || 'Failed')}</div>`; btn.disabled = false; btn.textContent = 'Reclaim deposit'; }
  });
}

/* ---------------- equity ---------------- */
async function renderEquityPanel(p) {
  const el = document.getElementById('equityPanel');
  if (!el || (p.equity_status !== 'active' && p.equity_status !== 'bought_back')) return;
  const r = await api(`/api/projects/${p.id}/equity`);
  if (!r.ok) return;
  const e = r.data;
  const isOwner = store.user && store.user.id === p.owner_id;
  let html = `<div class="panel"><h3>OWNERSHIP · ${e.backer_count} MEMBER-OWNERS</h3>
    <p style="color:var(--mut);font-size:14px;margin-bottom:12px">Each backer owns an equal share:
    <b style="color:var(--gold)">${e.share_pct_each}%</b> — and no member can own more than
    <b>1%</b> of any member's company. The founder can buy back every share at
    <b>cost + 10%</b>, or pay a <b>10% annual dividend</b> instead.</p>
    <table class="tbl"><tr><th>Backer</th><th>Pledged</th><th>Share</th><th>Buyback value</th></tr>
    ${e.backers.map(b => `<tr><td>${esc(b.name)}</td><td>${money(b.pledged_cents)}</td><td>${b.share_pct}%</td><td>${money(b.buyback_cents)}</td></tr>`).join('')}
    </table>`;
  if (e.equity_status === 'bought_back') {
    html += `<div class="okmsg" style="margin-top:12px">Founder bought back all member shares at cost + 10%.</div>`;
  } else if (isOwner) {
    html += `<div class="decide" style="margin-top:14px">
        <button class="btn small warn" id="buybackBtn">Buy back all shares (cost + 10%)</button>
        <button class="btn small ghost" id="dividendBtn">Pay 10% annual dividend</button>
      </div><div id="eqMsg" style="margin-top:10px"></div>
      ${e.last_dividend_at ? `<p style="color:var(--mut);font-size:13px">Last dividend paid ${esc(e.last_dividend_at.slice(0, 10))} — next one available a year later.</p>` : ''}
      <p class="hint" style="text-align:left">Recorded in the project's equity ledger. Actual payouts move through your own payment rails.</p>`;
  } else {
    html += `<p class="hint" style="text-align:left;margin-top:10px">Only the project founder can trigger a buyback or dividend.</p>`;
  }
  html += `</div>`;
  el.innerHTML = html;

  const bb = document.getElementById('buybackBtn');
  if (bb) bb.addEventListener('click', async () => {
    if (!confirm(`Buy back all ${e.backer_count} member shares at cost + 10%?`)) return;
    bb.disabled = true; bb.textContent = 'Recording…';
    const br = await api(`/api/projects/${p.id}/buyback`, { method: 'POST' });
    document.getElementById('eqMsg').innerHTML = br.ok
      ? `<div class="okmsg">Bought back — ${money(br.data.total_payout_cents)} recorded across ${br.data.backers} backers.</div>`
      : `<div class="err">${esc(br.data.error || 'Failed')}</div>`;
    if (br.ok) setTimeout(navigate, 1400); else { bb.disabled = false; bb.textContent = 'Buy back all shares (cost + 10%)'; }
  });
  const dv = document.getElementById('dividendBtn');
  if (dv) dv.addEventListener('click', async () => {
    if (!confirm('Record the 10% annual dividend for all member-owners?')) return;
    dv.disabled = true; dv.textContent = 'Recording…';
    const dr = await api(`/api/projects/${p.id}/dividend`, { method: 'POST' });
    document.getElementById('eqMsg').innerHTML = dr.ok
      ? `<div class="okmsg">Dividend recorded — ${money(dr.data.total_payout_cents)} across ${dr.data.backers} member-owners.</div>`
      : `<div class="err">${esc(dr.data.error || 'Failed')}</div>`;
    if (dr.ok) setTimeout(navigate, 1400); else { dv.disabled = false; dv.textContent = 'Pay 10% annual dividend'; }
  });
}

/* ---------------- submit ---------------- */
async function renderSubmit() {
  const u = await refreshMe();
  if (!u) { location.hash = '#/login'; return; }
  renderNav();
  const e = u.eligibility;
  if (!e.can_submit) {
    app.innerHTML = `<div class="dash"><div class="panel">
      <h3>NOT ELIGIBLE YET</h3>
      <p style="color:var(--mut)">To bring a project to the pool you need an active membership and 10 credited referrals ($100 in credits).</p>
      <ul style="color:var(--mut);padding-left:18px;margin-top:10px;display:grid;gap:6px">
        <li>${e.membership_active ? '✅' : '⬜'} Active $30/month membership</li>
        <li>${e.referrals_credited >= 10 ? '✅' : '⬜'} ${e.referrals_credited}/10 credited referrals</li>
      </ul>
      <p style="margin-top:14px"><a class="btn small" href="#/dashboard">Go to dashboard</a></p>
    </div></div>`;
    return;
  }
  app.innerHTML = `
  <div class="form-card" style="max-width:620px">
    <h2>Bring your project to the pool</h2>
    <p class="sub">Your max ask: <b style="color:var(--gold)">${money(e.max_goal_cents)}</b> · admin reviews every submission · needs <b>100 member backers</b> to fund, and each backer becomes a part-owner</p>
    <div id="msg"></div>
    <form id="f">
      <label>Project title</label><input name="title" required maxlength="80" placeholder="e.g. Reef — community solar for renters">
      <label>Tagline (one line)</label><input name="tagline" required maxlength="140" placeholder="What it is and who it's for">
      <label>Category</label>
      <select name="category">
        <option>startup</option><option>tech</option><option>food</option><option>health</option>
        <option>education</option><option>creative</option><option>community</option><option>general</option>
      </select>
      <label>Funding goal (USD, max ${money(e.max_goal_cents).replace('$','')})</label>
      <input name="goal_dollars" type="number" min="100" max="${e.max_goal_cents / 100}" step="1" required placeholder="e.g. 5000">
      <label>Full pitch — the problem, the plan, how the funds get used</label>
      <textarea name="description" required placeholder="Tell the pool why this deserves funding…"></textarea>
      <button class="btn" type="submit">Submit for review</button>
    </form>
  </div>`;
  document.getElementById('f').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const r = await api('/api/projects', { method: 'POST', body: JSON.stringify({
      title: fd.get('title'), tagline: fd.get('tagline'), description: fd.get('description'),
      category: fd.get('category'), goal_dollars: fd.get('goal_dollars'),
    })});
    if (r.ok) { location.hash = '#/project/' + r.data.id; }
    else document.getElementById('msg').innerHTML = `<div class="err">${esc(r.data.error || 'Failed')}</div>`;
  });
}

/* ---------------- admin ---------------- */
async function renderAdmin(tab) {
  const u = await refreshMe();
  if (!u) { location.hash = '#/login'; return; }
  if (!u.is_admin) { app.innerHTML = '<div class="dash"><div class="err">Admins only.</div></div>'; return; }
  renderNav();
  tab = tab || 'review';
  app.innerHTML = `
  <div class="dash">
    <h2>Admin dashboard 🐋</h2>
    <div class="tabs">
      <button data-t="review" class="${tab === 'review' ? 'on' : ''}">Review queue</button>
      <button data-t="members" class="${tab === 'members' ? 'on' : ''}">Members</button>
      <button data-t="stats" class="${tab === 'stats' ? 'on' : ''}">Stats</button>
    </div>
    <div id="tabBody"><span style="color:var(--mut)">Loading…</span></div>
  </div>`;
  document.querySelectorAll('.tabs button').forEach(b =>
    b.addEventListener('click', () => renderAdmin(b.dataset.t)));
  const body = document.getElementById('tabBody');

  if (tab === 'review') {
    const r = await api('/api/admin/projects?status=pending_review');
    const items = r.ok ? r.data : [];
    body.innerHTML = items.length ? items.map(p => `
      <div class="panel" style="margin-bottom:12px">
        <div class="rowflex"><b style="font-size:18px">${esc(p.title)}</b>
          <span style="color:var(--mut);font-size:13px">ask ${money(p.goal_cents)} · by ${esc(p.owner_name)}</span></div>
        <div style="color:var(--cyan);font-size:12px;font-weight:800;letter-spacing:.1em;margin:6px 0">${esc((p.category || '').toUpperCase())}</div>
        <div style="color:var(--mut);font-size:14px;margin-bottom:6px">${esc(p.tagline)}</div>
        <p style="white-space:pre-wrap;font-size:14px">${esc(p.description)}</p>
        <div class="decide">
          <button class="btn small" data-a="approve" data-id="${p.id}">Approve → goes live</button>
          <button class="btn small ghost" data-a="reject" data-id="${p.id}">Reject</button>
          <input data-note="${p.id}" placeholder="Note to founder (optional)" style="flex:1;min-width:200px">
        </div>
      </div>`).join('')
      : '<div class="panel"><p style="color:var(--mut)">Queue is clear. Nothing waiting for review.</p></div>';
    body.querySelectorAll('button[data-a]').forEach(b => b.addEventListener('click', async () => {
      const note = body.querySelector(`input[data-note="${b.dataset.id}"]`).value;
      b.disabled = true;
      const r = await api(`/api/admin/projects/${b.dataset.id}/decision`, {
        method: 'POST', body: JSON.stringify({ decision: b.dataset.a, note }),
      });
      if (r.ok) renderAdmin('review'); else { alert(r.data.error || 'Failed'); b.disabled = false; }
    }));
  } else if (tab === 'members') {
    const r = await api('/api/admin/users');
    const users = r.ok ? r.data : [];
    body.innerHTML = `<div class="panel"><table class="tbl">
      <tr><th>Member</th><th>Email</th><th>Status</th><th>Credits</th><th>Credited refs</th><th>Joined</th></tr>
      ${users.map(x => `<tr><td><b>${esc(x.name)}</b>${x.is_admin ? ' 👑' : ''}</td><td>${esc(x.email)}</td>
        <td><span class="pill ${x.membership_status}">${esc(x.membership_status.toUpperCase())}</span></td>
        <td>${money(x.referral_credits_cents)}</td><td>${x.credited_referrals}</td>
        <td style="color:var(--mut)">${esc((x.created_at || '').slice(0, 10))}</td></tr>`).join('')}
    </table></div>`;
  } else {
    const r = await api('/api/admin/stats');
    const s = r.ok ? r.data : {};
    setModeBadge(s.mode);
    const cards = [
      ['Total members', s.total_members || 0], ['Active members', s.active_members || 0],
      ['Weekly recurring', money(s.weekly_mrr_cents || 0)], ['Credits outstanding', money(s.referral_credits_outstanding_cents || 0)],
      ['Pending review', s.projects_pending || 0], ['Live now', s.projects_live || 0],
      ['Funded all-time', s.projects_funded || 0], ['Total pledged', money(s.total_pledged_cents || 0)],
    ];
    body.innerHTML = `<div class="cards">${cards.map(c =>
      `<div class="panel"><h3>${c[0].toUpperCase()}</h3><div class="bignum" style="font-size:32px">${c[1]}</div></div>`).join('')}</div>`;
  }
}

/* ---------------- router ---------------- */
async function navigate() {
  const { segs, query } = parseHash();
  renderNav();
  window.scrollTo(0, 0);
  const [page, arg] = segs;
  if (!page) return renderLanding();
  if (page === 'join') return renderJoin(query);
  if (page === 'login') return renderLogin();
  if (page === 'dashboard') return renderDashboard(query);
  if (page === 'projects') return renderProjects();
  if (page === 'project' && arg) return renderProjectDetail(arg, query);
  if (page === 'submit') return renderSubmit();
  if (page === 'admin') return renderAdmin(query.tab);
  return renderLanding();
}

window.addEventListener('hashchange', navigate);
(async function init() {
  if (store.token) await refreshMe();
  renderNav();
  navigate();
})();
