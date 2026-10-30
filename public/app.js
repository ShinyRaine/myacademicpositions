/* Position Radar dashboard.
 *
 * Reads two JSON files the GitHub Actions workflows commit:
 *   jobs.json      scored vacancies, newest scan
 *   decisions.json triage state read back off the GitHub issue labels
 * There is no backend: the site is static and the issues are the source of
 * truth for every decision.
 */

const TIER_ORDER = { A: 0, B: 1, C: 2, D: 3 };
// Overwritten from jobs.json, so renaming a tier in profile.yaml renames it here.
let TIER_NAME = { A: '对口', B: '值得细看', C: '擦边', D: '不相关' };
const DECISION_LABEL = {
  new: '未决策', maybe: '待定', apply: '要投', applied: '已投', skip: '跳过',
};
const VERDICT_LABEL = { apply: '建议投', maybe: '可考虑', skip: '不建议' };
const SOURCE_LABEL = { ap: 'Academic Positions', kth: 'KTH 官网', chalmers: 'Chalmers 官网' };

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
      job.contact, job.department, job.source,
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
// The collapsed card shows only facts — when it closes and what you decided.
// The rule band on the left and Gemini's recommendation are two different
// judgements; putting both in one row made them look like one contradictory
// score, so the recommendation lives inside the card.
function badges(job) {
  const dec = decisionOf(job);
  return [
    `<span class="badge deadline${job.urgent ? ' urgent' : ''}">${esc(deadlineText(job))}</span>`,
    `<span class="badge d-${esc(dec.decision)}">${esc(DECISION_LABEL[dec.decision] || dec.decision)}</span>`,
  ].join('');
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

  const left = v ? list('匹配点', v.why_match) + list('保留意见', v.why_not) : '';
  const right = v ? list('硬性障碍', v.blockers, 'blockers') + list('投之前要准备', v.prep) : '';

  const tierName = esc(job.tier_name || TIER_NAME[job.tier] || job.tier);
  const verdict = v
    ? `<div class="verdict v-${esc(v.verdict)}">
         <p class="vhead">${esc(v.model || 'Gemini')} 读完整篇广告后的建议</p>
         <p class="vmain"><b>${esc(VERDICT_LABEL[v.verdict] || v.verdict)}</b>
            <span class="vfit">与你的背景匹配 ${v.fit}/10</span></p>
         ${v.headline ? `<p class="vsay">“${esc(v.headline)}”</p>` : ''}
         <p class="vnote">左边的「${tierName} ${job.score}」是另一回事：那是关键词命中加机构、
            国家权重算出来的规则分，只用来决定这条值不值得花钱细读。两者不一致很正常，
            以这里的理由为准——但理由本身也要自己核对。</p>
       </div>`
    : `<div class="verdict v-none">
         <p class="vhead">没有 LLM 研判</p>
         <p class="vnote">这条的规则分「${tierName} ${job.score}」没到研判阈值，
            或者调用失败、免费额度用完了。下面的规则分构成是它得分的全部依据。</p>
       </div>`;

  const issueBtn = dec.issue_url
    ? `<a class="btn" href="${esc(dec.issue_url)}" target="_blank" rel="noreferrer">在 Issue #${dec.issue} 上决策</a>`
    : (state.repo
      ? `<a class="btn" href="https://github.com/${esc(state.repo)}/issues?q=${encodeURIComponent(job.id)}" target="_blank" rel="noreferrer">查找 Issue</a>`
      : '');

  const alt = (job.also_posted_as || []).length
    ? `<p class="alt">同一岗位的其他语言版本：${job.also_posted_as.map((a) =>
      `<a href="${esc(a.url)}" target="_blank" rel="noreferrer">${esc(a.language)}</a>`).join('、')}</p>`
    : '';

  const dup = (job.also_listed_on || []).length
    ? `<p class="alt">也出现在：${job.also_listed_on.map((a) =>
      `<a href="${esc(a.url)}" target="_blank" rel="noreferrer">${
        esc(SOURCE_LABEL[a.source] || a.source)}</a>`).join('、')}</p>`
    : '';

  const contactMail = (job.contact || '').match(/[\w.+-]+@[\w.-]+\.\w+/);
  const contact = job.contact
    ? `<p class="summary"><b>招聘联系人：</b>${esc(job.contact.replace(/\s+/g, ' '))}${
        contactMail ? ` <a href="mailto:${esc(contactMail[0])}">写信</a>` : ''}</p>`
    : '';

  return `
    <div class="detail">
      ${job.summary ? `<p class="summary">${esc(job.summary)}</p>` : ''}
      ${v && v.group_or_supervisor ? `<p class="summary"><b>组 / 导师：</b>${esc(v.group_or_supervisor)}</p>` : ''}
      ${contact}
      ${job.department ? `<p class="summary"><b>部门：</b>${esc(job.department)}</p>` : ''}
      ${verdict}
      ${left || right ? `<div class="cols"><div>${left}</div><div>${right}</div></div>` : ''}
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
      ${dup}
    </div>`;
}

function card(job) {
  const dec = decisionOf(job);
  return `
    <article class="card tier-${esc(job.tier)} decided-${esc(dec.decision)}" data-id="${esc(job.id)}">
      <div class="head" role="button" tabindex="0">
        <div class="scorebox"><span class="n">${job.score}</span><span class="t">${
            esc(job.tier_name || TIER_NAME[job.tier] || job.tier)}</span></div>
        <div>
          <p class="title">${esc(job.title)}</p>
          <p class="where"><b>${esc(job.employer)}</b> · ${esc(job.city)}${
            job.country ? `, ${esc(job.country)}` : ''}${
            job.source ? ` <span class="src">${esc(SOURCE_LABEL[job.source] || job.source)}</span>` : ''}</p>
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

/* ------------------------------------------------------------------ init */
(async function init() {
  const [data, dec] = await Promise.all([
    loadJSON('./jobs.json', { jobs: [], counts: {} }),
    loadJSON('./decisions.json', { decisions: {} }),
  ]);

  state.jobs = data.jobs || [];
  state.decisions = dec.decisions || {};
  state.repo = data.repo || null;
  if (data.tiers) {
    TIER_NAME = Object.fromEntries(
      Object.entries(data.tiers).map(([k, v]) => [k, v.name || k]));
  }
  document.querySelectorAll('.chip[data-filter="tier"]').forEach((chip) => {
    chip.textContent = TIER_NAME[chip.dataset.value] || chip.dataset.value;
  });

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
