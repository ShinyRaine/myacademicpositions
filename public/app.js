/* Position Radar dashboard.
 *
 * Reads two JSON files the GitHub Actions workflows commit:
 *   jobs.json      scored vacancies, newest scan
 *   decisions.json triage state read back off the GitHub issue labels
 * There is no backend: the site is static and the issues are the source of
 * truth for every decision.
 */

const TIER_ORDER = { A: 0, B: 1, C: 2, D: 3 };
const DECISION_LABEL = {
  new: '未决策', maybe: '待定', apply: '要投', applied: '已投', skip: '跳过',
};
const VERDICT_LABEL = { apply: '建议投', maybe: '可考虑', skip: '不建议' };

const state = {
  jobs: [],
  decisions: {},
  repo: null,
  q: '',
  tiers: new Set(['A', 'B', 'C']),
  flags: new Set(),
  sort: 'tier',
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

async function loadJSON(path, fallback) {
  try {
    const res = await fetch(path, { cache: 'no-store' });
    if (!res.ok) throw new Error(res.status);
    return await res.json();
  } catch (err) {
    console.warn('could not load', path, err);
    return fallback;
  }
}

function deadlineText(job) {
  if (job.days_left === null || job.days_left === undefined) return '无截止日';
  const d = (job.deadline || '').slice(0, 10);
  if (job.days_left < 0) return `${d} 已过`;
  if (job.days_left === 0) return `${d} 今天截止`;
  return `${d} · 剩 ${job.days_left} 天`;
}

function decisionOf(job) {
  return state.decisions[job.id] || { decision: 'new' };
}

/* ---------------------------------------------------------------- filter */
function visible() {
  const q = state.q.trim().toLowerCase();
  return state.jobs.filter((job) => {
    if (!state.tiers.has(job.tier)) return false;

    const dec = decisionOf(job);
    if (state.flags.has('urgent') && !job.urgent) return false;
    if (state.flags.has('undecided') && dec.decision !== 'new') return false;
    if (state.flags.has('sweden') && !/sweden/i.test(job.country || '')) return false;

    if (!q) return true;
    const hay = [
      job.title, job.employer, job.city, job.country, job.summary,
      job.llm && job.llm.headline, job.llm && job.llm.group_or_supervisor,
      ...(job.breakdown || []).flatMap((b) => b.hits || []),
    ].join(' ').toLowerCase();
    return hay.includes(q);
  });
}

function sorted(jobs) {
  const fit = (j) => (j.llm ? j.llm.fit : -1);
  const copy = jobs.slice();
  switch (state.sort) {
    case 'score':
      return copy.sort((a, b) => b.score - a.score);
    case 'fit':
      return copy.sort((a, b) => fit(b) - fit(a) || b.score - a.score);
    case 'deadline':
      return copy.sort((a, b) => {
        const x = a.days_left ?? 1e6; const y = b.days_left ?? 1e6;
        return x - y;
      });
    case 'new':
      return copy.sort((a, b) => String(b.first_seen || '').localeCompare(String(a.first_seen || '')));
    default:
      return copy.sort((a, b) => (
        TIER_ORDER[a.tier] - TIER_ORDER[b.tier] || fit(b) - fit(a) || b.score - a.score
      ));
  }
}

/* ---------------------------------------------------------------- render */
function badges(job) {
  const out = [];
  out.push(`<span class="badge deadline${job.urgent ? ' urgent' : ''}">${esc(deadlineText(job))}</span>`);
  if (job.llm) {
    out.push(`<span class="badge v-${esc(job.llm.verdict)}">${
      esc(VERDICT_LABEL[job.llm.verdict] || job.llm.verdict)} ${job.llm.fit}/10</span>`);
  }
  const dec = decisionOf(job);
  out.push(`<span class="badge d-${esc(dec.decision)}">${esc(DECISION_LABEL[dec.decision] || dec.decision)}</span>`);
  return out.join('');
}

function list(title, items, cls = '') {
  if (!items || !items.length) return '';
  return `<h4>${esc(title)}</h4><ul class="${cls}">${
    items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
}

function detail(job) {
  const v = job.llm;
  const dec = decisionOf(job);
  const hits = (job.breakdown || []).filter((b) => (b.hits || []).length);
  const gate = (job.breakdown || []).find((b) => b.kind === 'gate');

  const left = v
    ? list('匹配点', v.why_match) + list('保留意见', v.why_not)
    : '<h4>研判</h4><p class="summary">这条没有 LLM 研判（低于阈值，或调用失败）。</p>';
  const right = v
    ? list('硬性障碍', v.blockers, 'blockers') + list('投之前要准备', v.prep)
    : '';

  const issueBtn = dec.issue_url
    ? `<a class="btn" href="${esc(dec.issue_url)}" target="_blank" rel="noreferrer">在 Issue #${dec.issue} 上决策</a>`
    : (state.repo
      ? `<a class="btn" href="https://github.com/${esc(state.repo)}/issues?q=${encodeURIComponent(job.id)}" target="_blank" rel="noreferrer">查找 Issue</a>`
      : '');

  const alt = (job.also_posted_as || []).length
    ? `<p class="alt">同一岗位的其他语言版本：${job.also_posted_as.map((a) =>
      `<a href="${esc(a.url)}" target="_blank" rel="noreferrer">${esc(a.language)}</a>`).join('、')}</p>`
    : '';

  return `
    <div class="detail">
      ${job.summary ? `<p class="summary">${esc(job.summary)}</p>` : ''}
      ${v && v.group_or_supervisor ? `<p class="summary"><b>组 / 导师：</b>${esc(v.group_or_supervisor)}</p>` : ''}
      <div class="cols"><div>${left}</div><div>${right}</div></div>
      ${hits.length ? `<h4>规则分构成</h4><div class="hits">${hits.map((b) => {
        const applied = b.applied ?? b.weight;
        const scaled = b.factor !== undefined && b.factor !== 1 && b.weight > 0;
        return `${esc(b.label)} <code>${applied > 0 ? '+' : ''}${applied}</code>${
          scaled ? ` <span title="主题闸门">(${b.weight > 0 ? '+' : ''}${b.weight}×${b.factor})</span>` : ''
        } — ${esc((b.hits || []).join(', '))}`;
      }).join('<br>')}${
        gate && gate.note ? `<br><span title="机构分不能压过课题相关度">闸门 — ${esc(gate.note)}</span>` : ''
      }</div>` : ''}
      <div class="actions">
        <a class="btn primary" href="${esc(job.url)}" target="_blank" rel="noreferrer">打开招聘广告 ↗</a>
        ${issueBtn}
      </div>
      ${alt}
    </div>`;
}

function card(job) {
  const dec = decisionOf(job);
  return `
    <article class="card tier-${esc(job.tier)} decided-${esc(dec.decision)}" data-id="${esc(job.id)}">
      <div class="head" role="button" tabindex="0">
        <div class="scorebox"><span class="n">${job.score}</span><span class="t">${esc(job.tier)}</span></div>
        <div>
          <p class="title">${esc(job.title)}</p>
          <p class="where"><b>${esc(job.employer)}</b> · ${esc(job.city)}${
            job.country ? `, ${esc(job.country)}` : ''}</p>
          ${job.llm && job.llm.headline
            ? `<p class="verdictline"><span class="vq">“${esc(job.llm.headline)}”</span></p>` : ''}
        </div>
        <div class="badges">${badges(job)}</div>
      </div>
      ${detail(job)}
    </article>`;
}

function render() {
  const jobs = sorted(visible());
  const board = $('#board');
  board.innerHTML = jobs.length
    ? jobs.map(card).join('')
    : '<p class="empty">没有符合当前筛选的岗位。</p>';

  board.querySelectorAll('.head').forEach((head) => {
    const toggle = () => head.parentElement.classList.toggle('open');
    head.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;
      toggle();
    });
    head.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  });
}

function renderTallies(data) {
  const decided = Object.values(state.decisions);
  const apply = decided.filter((d) => d.decision === 'apply' || d.decision === 'applied').length;
  const undecided = state.jobs.filter((j) => decisionOf(j).decision === 'new'
    && TIER_ORDER[j.tier] <= 1).length;
  const c = data.counts || {};
  $('#tallies').innerHTML = [
    ['t-a', c.tier_A ?? 0, 'A 档'],
    ['t-b', c.tier_B ?? 0, 'B 档'],
    ['t-urgent', c.urgent ?? 0, '快截止'],
    ['', undecided, 'A/B 待决策'],
    ['t-apply', apply, '决定要投'],
  ].map(([cls, n, label]) =>
    `<div class="tally ${cls}"><b>${n}</b><span>${label}</span></div>`).join('');
}

/* ------------------------------------------------------------------ init */
(async function init() {
  const [data, dec] = await Promise.all([
    loadJSON('./jobs.json', { jobs: [], counts: {} }),
    loadJSON('./decisions.json', { decisions: {} }),
  ]);

  state.jobs = data.jobs || [];
  state.decisions = dec.decisions || {};
  state.repo = data.repo || null;

  const when = data.generated_at
    ? new Date(data.generated_at).toLocaleString('sv-SE', { timeZone: 'Europe/Stockholm' })
    : '未知';
  $('#meta').textContent =
    `${state.jobs.length} 个在招岗位 · 最后扫描 ${when} (Stockholm)`;

  if (state.repo) {
    $('#repolink').innerHTML =
      `仓库 <a href="https://github.com/${esc(state.repo)}" rel="noreferrer">${esc(state.repo)}</a> · ` +
      `<a href="https://github.com/${esc(state.repo)}/issues?q=is%3Aopen+label%3Aradar" rel="noreferrer">待决策的 Issue</a>`;
  }

  renderTallies(data);

  $('#q').addEventListener('input', (e) => { state.q = e.target.value; render(); });
  $('#sort').addEventListener('change', (e) => { state.sort = e.target.value; render(); });
  $('#filters').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    const { filter, value } = chip.dataset;
    const bag = filter === 'tier' ? state.tiers : state.flags;
    if (bag.has(value)) bag.delete(value); else bag.add(value);
    chip.classList.toggle('on');
    render();
  });

  render();
})();
