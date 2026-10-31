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

const MATERIAL_STATUS = { todo: '待办', doing: '进行中', done: '完成', na: '不适用' };
// Only for the waiting text before the server has said which model it used.
const CV_MODEL = 'gemini-3.8-flash';

const state = {
  jobs: [],
  decisions: {},
  applications: {},   // jobId -> { status, done, total, issue_url, error, cv }
  details: {},        // jobId -> { application, materials } once expanded
  busy: new Set(),
  cvBusy: new Set(),  // the CV is a second, much slower call; it has its own
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

/* ------------------------------------------------------------------ api */
// Writes carry x-radar-key only if one has been set. The deployment accepts
// writes without it unless RADAR_API_KEY is configured there.
function apiKey() {
  try { return localStorage.getItem('radarKey') || ''; } catch { return ''; }
}

async function api(path, { method = 'GET', body, raw, headers = {} } = {}) {
  const key = apiKey();
  const res = await fetch(`/api/${path}`, {
    method,
    headers: {
      ...(raw ? {} : body ? { 'Content-Type': 'application/json' } : {}),
      ...(key ? { 'x-radar-key': key } : {}),
      ...headers,
    },
    body: raw || (body ? JSON.stringify(body) : undefined),
  });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text.slice(0, 300) }; }
  if (res.status === 401) {
    const given = window.prompt('这个部署设了写入口令，请输入：');
    if (given) {
      try { localStorage.setItem('radarKey', given); } catch { /* private window */ }
      return api(path, { method, body, raw, headers });
    }
  }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

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
    // ctx:<label>[,<label>…] keeps only jobs carrying one of those context
    // flags. They score nothing, so filtering is the only way they act.
    for (const f of state.flags) {
      if (!f.startsWith('ctx:')) continue;
      const want = f.slice(4).split(',');
      if (!(job.flags || []).some((g) => want.includes(g.label))) return false;
    }

    if (!q) return true;
    const hay = [
      job.title, job.employer, job.city, job.country, job.summary,
      job.llm && job.llm.headline, job.llm && job.llm.group_or_supervisor,
      job.contact, job.department, job.source,
      ...(job.breakdown || []).flatMap((b) => b.hits || []),
      ...(job.flags || []).flatMap((f) => [f.name, ...(f.hits || [])]),
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
  const app = state.applications[job.id];
  const out = [
    `<span class="badge deadline${job.urgent ? ' urgent' : ''}">${esc(deadlineText(job))}</span>`,
  ];
  // Context flags carry no points — they are here to be seen and filtered on,
  // which is the whole reason they were taken out of the score.
  for (const f of job.flags || []) {
    const why = esc(`${(f.hits || []).join(', ')}（不计分）`);
    out.push(`<span class="badge flag f-${esc(f.severity || 'note')}" title="${why}">${esc(f.name)}</span>`);
  }
  if (app) {
    const cls = app.status === 'failed' ? 'prep-bad'
      : app.total && app.done === app.total ? 'prep-done' : 'prep';
    const label = app.status === 'failed' ? '准备失败'
      : app.status === 'preparing' ? '准备中…'
      : `材料 ${app.done}/${app.total}`;
    out.push(`<span class="badge ${cls}">${esc(label)}</span>`);
  }
  out.push(`<span class="badge d-${esc(dec.decision)}">${esc(DECISION_LABEL[dec.decision] || dec.decision)}</span>`);
  return out.join('');
}

/* ------------------------------------------------------- preparation pane */
function materialRow(jobId, m) {
  const buttons = Object.entries(MATERIAL_STATUS).map(([k, label]) =>
    `<button class="mbtn${m.status === k ? ' on' : ''}" data-job="${esc(jobId)}"
       data-key="${esc(m.key)}" data-status="${k}">${esc(label)}</button>`).join('');
  return `
    <li class="material s-${esc(m.status)}">
      <div class="mhead">
        <span class="mname">${esc(m.name)}${m.required ? '' : ' <i>可选</i>'}</span>
        <span class="mbtns">${buttons}</span>
      </div>
      ${m.what ? `<p class="mwhat"><b>广告要求</b>${esc(m.what)}</p>` : ''}
      ${m.advice ? `<p class="madvice"><b>怎么准备</b>${esc(m.advice)}</p>` : ''}
    </li>`;
}

// The CV is written by a model that thinks for minutes, so it gets its own
// line here rather than being folded into the checklist's state: the checklist
// is usable long before the CV lands.
function cvLine(jobId, cv = {}) {
  if (state.cvBusy.has(jobId)) {
    return `<p class="summary">定制简历生成中…（${esc(cv.model || CV_MODEL)}，一次 1-5 分钟，
      关掉页面这次就断了，材料清单不受影响）</p>`;
  }
  if (cv.status === 'running') {
    // Written when the call started and never updated: the page went away
    // before the answer came back, or the function hit its ceiling.
    return '<p class="mwarn">上一次简历生成没等到结果（页面关了，或者超过 5 分钟）。'
      + '点「重新生成简历」再来一次。</p>';
  }
  if (cv.status === 'ready') {
    return `<p class="summary">定制简历已写进 Issue${cv.model ? `（${esc(cv.model)} 生成）` : ''}。</p>`;
  }
  if (cv.status === 'failed') {
    return `<p class="mwarn">定制简历没生成成：${esc(cv.error || '原因不明')}</p>`;
  }
  if (!cv.status) {
    // Prepared before the CV moved into its own call: whatever is in the issue
    // came from the checklist model, and nothing here recorded it.
    return '<p class="summary">这条记录比两步流程早，简历状态没记下来——'
      + 'Issue 里那份是旧模型写的。点「重新生成简历」换成新的。</p>';
  }
  return '<p class="summary">还没有定制简历。</p>';
}

function prepPane(job) {
  const app = state.applications[job.id];
  const loaded = state.details[job.id];
  const busy = state.busy.has(job.id);

  if (busy) {
    return `<div class="prep"><h4>准备投递</h4>
      <p class="summary">正在读广告、生成材料清单…（约 5-15 秒，定制简历随后单独生成）</p></div>`;
  }

  if (!app) {
    return `<div class="prep"><h4>准备投递</h4>
      <p class="summary">点下面这个按钮会：读这则广告列出它要求的每份材料、建一个 GitHub Issue
        把清单放进去并打上 <code>decision:apply</code>，然后用你的基础简历生成一份针对本岗位的
        定制稿补进同一个 Issue。清单几秒就有，简历用的是慢模型，要 1-5 分钟。
        材料的准备进度在这里勾。</p>
      <div class="actions">
        <button class="btn primary act-prepare" data-job="${esc(job.id)}">开始准备投递</button>
      </div></div>`;
  }

  const mats = loaded?.materials || [];
  const pct = app.total ? Math.round((app.done / app.total) * 100) : 0;
  return `
    <div class="prep">
      <h4>准备投递</h4>
      <div class="prepbar"><span style="width:${pct}%"></span></div>
      <p class="summary">
        ${app.total ? `${app.done} / ${app.total} 份材料已完成。` : '还没有材料清单。'}
        ${app.issue_url ? `<a href="${esc(app.issue_url)}" target="_blank" rel="noreferrer">Issue #${app.issue} ↗</a>（定制简历在里面）` : ''}
      </p>
      ${app.error ? `<p class="mwarn">上次有一步没成：${esc(app.error)}</p>` : ''}
      ${cvLine(job.id, app.cv || {})}
      ${mats.length ? `<ul class="materials">${mats.map((m) => materialRow(job.id, m)).join('')}</ul>`
        : '<p class="summary">展开时会载入清单…</p>'}
      <div class="actions">
        <button class="btn act-prepare" data-job="${esc(job.id)}">重新生成清单</button>
        <button class="btn act-tailor" data-job="${esc(job.id)}"
          ${state.cvBusy.has(job.id) ? 'disabled' : ''}>重新生成简历</button>
        ${app.issue_url ? `<a class="btn" href="${esc(app.issue_url)}" target="_blank" rel="noreferrer">打开 Issue</a>` : ''}
      </div>
    </div>`;
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
  const flags = job.flags || [];

  const left = v ? list('匹配点', v.why_match) + list('保留意见', v.why_not) : '';
  const right = v ? list('硬性障碍', v.blockers, 'blockers') + list('投之前要准备', v.prep) : '';

  const tierName = esc(job.tier_name || TIER_NAME[job.tier] || job.tier);
  const verdict = v
    ? `<div class="verdict v-${esc(v.verdict)}">
         <p class="vhead">${esc(v.model || 'Gemini')} 读完整篇广告后的建议</p>
         <p class="vmain"><b>${esc(VERDICT_LABEL[v.verdict] || v.verdict)}</b>
            <span class="vfit">与你的背景匹配 ${v.fit}/10</span></p>
         ${v.headline ? `<p class="vsay">“${esc(v.headline)}”</p>` : ''}
       </div>`
    : `<div class="verdict v-none">
         <p class="vhead">没有 LLM 研判</p>
         <p class="vnote">这条的课题匹配分「${tierName} ${job.score}」没到研判阈值，
            或者调用失败、免费额度用完了。下面的构成是它得分的全部依据。</p>
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
      ${hits.length ? `<h4>课题匹配构成 <i>满分 100，只看研究内容</i></h4><div class="hits">${
        hits.map((b) => {
          const applied = b.applied ?? b.weight;
          const scaled = b.factor !== undefined && b.factor !== 1 && b.weight > 0;
          return `${esc(b.label)} <code>${applied > 0 ? '+' : ''}${applied}</code>${
            scaled ? ` <span title="命中位置决定吃到几成权重">(${b.weight}×${b.factor}${
              b.note ? `，${esc(b.note)}` : ''})</span>` : ''
          } — ${esc((b.hits || []).join(', '))}`;
        }).join('<br>')
      }</div>` : '<h4>课题匹配构成</h4><p class="summary">一个课题关键词都没命中。</p>'}
      ${flags.length ? `<h4>上下文 <i>不计分</i></h4><div class="hits">${flags.map((f) => (
        `${esc(f.name)} <code>0</code> — ${esc((f.hits || []).join(', '))}`
      )).join('<br>')}</div>` : ''}
      <div class="actions">
        <a class="btn primary" href="${esc(job.url)}" target="_blank" rel="noreferrer">打开招聘广告 ↗</a>
        ${issueBtn}
      </div>
      ${prepPane(job)}
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

/** Redraw one card without disturbing the rest of the board. */
function rerender(jobId) {
  const el = document.querySelector(`.card[data-id="${CSS.escape(jobId)}"]`);
  const job = state.jobs.find((j) => j.id === jobId);
  if (!el || !job) return;
  const open = el.classList.contains('open');
  el.outerHTML = card(job);
  const fresh = document.querySelector(`.card[data-id="${CSS.escape(jobId)}"]`);
  if (fresh && open) fresh.classList.add('open');
}

async function openDetails(jobId) {
  if (state.details[jobId] || !state.applications[jobId]) return;
  try {
    const data = await api(`application?jobId=${encodeURIComponent(jobId)}`);
    state.details[jobId] = data;
    rerender(jobId);
  } catch (err) {
    console.warn('could not load the checklist', err);
  }
}

async function prepare(jobId) {
  if (state.busy.has(jobId)) return;
  state.busy.add(jobId);
  rerender(jobId);
  let cvStatus = null;
  try {
    const data = await api('prepare', { method: 'POST', body: { jobId } });
    state.details[jobId] = data;
    const total = (data.materials || []).length;
    cvStatus = data.application?.cv_status || null;
    state.applications[jobId] = {
      status: data.application?.status || 'ready',
      issue: data.application?.issue_number,
      issue_url: data.application?.issue_url,
      total,
      done: (data.materials || []).filter((m) => m.status === 'done').length,
      error: data.issue_error || data.application?.error || null,
      cv: {
        status: cvStatus,
        model: data.application?.cv_model || null,
        error: data.application?.cv_error || null,
      },
    };
  } catch (err) {
    state.applications[jobId] = {
      status: 'failed', total: 0, done: 0, error: String(err.message || err),
    };
  } finally {
    state.busy.delete(jobId);
    rerender(jobId);
  }
  // Second stage: a CV already written for this vacancy is kept, anything else
  // is (re)generated now that the checklist is on screen.
  if (cvStatus && cvStatus !== 'ready') tailor(jobId);
}

async function tailor(jobId, force = false) {
  const app = state.applications[jobId];
  if (!app || state.cvBusy.has(jobId)) return;
  state.cvBusy.add(jobId);
  app.cv = { ...(app.cv || {}), status: 'running', error: null };
  rerender(jobId);
  try {
    const data = await api(`tailor${force ? '?force=1' : ''}`, { method: 'POST', body: { jobId } });
    app.cv = {
      status: data.application?.cv_status || 'ready',
      model: data.model || data.application?.cv_model || null,
      error: data.issue_error || data.application?.cv_error || null,
    };
    if (data.application?.issue_url) {
      app.issue = data.application.issue_number;
      app.issue_url = data.application.issue_url;
    }
  } catch (err) {
    app.cv = { ...(app.cv || {}), status: 'failed', error: String(err.message || err) };
  } finally {
    state.cvBusy.delete(jobId);
    rerender(jobId);
  }
}

async function setMaterial(jobId, key, status) {
  try {
    const data = await api('application', { method: 'PATCH', body: { jobId, key, status } });
    state.details[jobId] = { ...(state.details[jobId] || {}), materials: data.materials };
    const app = state.applications[jobId];
    if (app) {
      app.total = data.materials.length;
      app.done = data.materials.filter((m) => m.status === 'done').length;
    }
    rerender(jobId);
  } catch (err) {
    window.alert(`没改成：${err.message || err}`);
  }
}

function render() {
  const jobs = sorted(visible());
  const board = $('#board');
  board.innerHTML = jobs.length
    ? jobs.map(card).join('')
    : '<p class="empty">没有符合当前筛选的岗位。</p>';
}

// One delegated listener on the board, so a re-rendered card keeps working.
function wireBoard() {
  const board = $('#board');

  board.addEventListener('click', (e) => {
    const prep = e.target.closest('.act-prepare');
    if (prep) { prepare(prep.dataset.job); return; }

    const tail = e.target.closest('.act-tailor');
    if (tail) { tailor(tail.dataset.job, true); return; }

    const mbtn = e.target.closest('.mbtn');
    if (mbtn) { setMaterial(mbtn.dataset.job, mbtn.dataset.key, mbtn.dataset.status); return; }

    const head = e.target.closest('.head');
    if (!head || e.target.closest('a')) return;
    const cardEl = head.parentElement;
    cardEl.classList.toggle('open');
    if (cardEl.classList.contains('open')) openDetails(cardEl.dataset.id);
  });

  board.addEventListener('keydown', (e) => {
    const head = e.target.closest('.head');
    if (!head || (e.key !== 'Enter' && e.key !== ' ')) return;
    e.preventDefault();
    const cardEl = head.parentElement;
    cardEl.classList.toggle('open');
    if (cardEl.classList.contains('open')) openDetails(cardEl.dataset.id);
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

  wireBoard();
  wireCvPanel();
  render();

  // The board is useful without the backend, so a missing API is not fatal.
  api('applications')
    .then((d) => { state.applications = d.applications || {}; render(); })
    .catch((err) => console.warn('preparation state unavailable', err));

  api('cv').then(showCv).catch(() => {});
})();

/* ------------------------------------------------------------- CV panel */
function showCv(data) {
  const cv = data && data.cv;
  const el = $('#cvstate');
  if (!el) return;
  el.textContent = cv
    ? `${cv.filename} · ${(cv.size_bytes / 1024).toFixed(0)} KB · ${
      new Date(cv.uploaded_at).toLocaleDateString('sv-SE')}`
    : '还没有上传基础简历。定制简历会只靠画像生成，质量会差很多。';
  $('#cvpanel').classList.toggle('has-cv', Boolean(cv));
}

function wireCvPanel() {
  const panel = $('#cvpanel');
  const toggle = $('#cvtoggle');
  if (!panel || !toggle) return;

  toggle.addEventListener('click', () => panel.classList.toggle('open'));

  $('#cvfile').addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    $('#cvstate').textContent = `正在上传 ${file.name}…`;
    try {
      const data = await api('cv', {
        method: 'POST',
        raw: await file.arrayBuffer(),
        headers: {
          'Content-Type': file.type || 'application/pdf',
          'x-cv-type': file.type || 'application/pdf',
          'x-cv-name': encodeURIComponent(file.name),
        },
      });
      showCv(data);
    } catch (err) {
      $('#cvstate').textContent = `上传失败：${err.message || err}`;
    } finally {
      e.target.value = '';
    }
  });
}
