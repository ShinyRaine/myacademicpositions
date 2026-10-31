// GitHub issue writing from the Vercel side.
//
// The scan workflow opens an issue for every shortlisted vacancy using the
// Actions token. This runs outside Actions, so it needs a PAT with issue write
// access on the repository, in GITHUB_TOKEN.

const API = 'https://api.github.com';

export const MARKER = '<!-- radar:job-id=';
const MAT_OPEN = '<!-- radar:materials -->';
const MAT_CLOSE = '<!-- /radar:materials -->';
const MATCH_OPEN = '<!-- radar:match -->';
const MATCH_CLOSE = '<!-- /radar:match -->';

function config() {
  const repo = process.env.GITHUB_REPOSITORY;
  const token = process.env.GITHUB_TOKEN;
  if (!repo || !token) {
    throw new Error('GITHUB_REPOSITORY and GITHUB_TOKEN must be set on this deployment');
  }
  return { repo, token };
}

async function gh(method, path, body) {
  const { repo, token } = config();
  const res = await fetch(`${API}/repos/${repo}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
      'User-Agent': 'position-radar',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`GitHub ${res.status} on ${method} ${path}: ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

/** The radar issue for this vacancy, or null. */
export async function findIssue(jobId) {
  for (let page = 1; page <= 5; page += 1) {
    const batch = await gh('GET', `/issues?state=all&labels=radar&per_page=100&page=${page}`);
    const hit = (batch || []).find(
      (i) => !i.pull_request && (i.body || '').includes(`${MARKER}${jobId} -->`),
    );
    if (hit) return hit;
    if (!batch || batch.length < 100) return null;
  }
  return null;
}

export function checklist(materials, extra = {}) {
  const lines = [MAT_OPEN, '', '### 申请材料', ''];
  if (!materials.length) {
    lines.push('_这则广告没有列出具体材料清单。_', '');
  }
  const box = { done: 'x', doing: ' ', todo: ' ', na: '-' };
  for (const m of materials) {
    const mark = box[m.status] ?? ' ';
    const flag = m.required ? '' : ' _(可选)_';
    const state = m.status === 'doing' ? ' — 进行中'
      : m.status === 'na' ? ' — 不适用' : '';
    lines.push(`- [${mark === '-' ? ' ' : mark}] **${m.name}**${flag}${state}`);
    if (m.what) lines.push(`  - 广告要求：${m.what}`);
    if (m.advice) lines.push(`  - 怎么准备：${m.advice}`);
  }
  lines.push('');
  if (extra.how_to_submit) lines.push(`**投递方式：** ${extra.how_to_submit}`, '');
  if (extra.deadline_note) lines.push(`**时间说明：** ${extra.deadline_note}`, '');
  lines.push(
    '_勾选状态由面板上的「准备投递」区同步，手动勾这里不会回传。_',
    '',
    MAT_CLOSE,
  );
  return lines.join('\n');
}

/**
 * Replace only the checklist block, so anything written into the issue by hand
 * or by the scan survives. Appends the block if it is not there yet.
 */
export function withChecklist(body, block) {
  const start = body.indexOf(MAT_OPEN);
  const end = body.indexOf(MAT_CLOSE);
  if (start !== -1 && end > start) {
    return body.slice(0, start) + block + body.slice(end + MAT_CLOSE.length);
  }
  return `${body.trimEnd()}\n\n---\n\n${block}\n`;
}

/**
 * The radar's own half of the issue, rendered here so that an issue opened
 * from the dashboard looks like one opened by the scan.
 *
 * It mirrors `match_block` in radar/issues.py, which owns the format and
 * rewrites this block on every scan. That is also why nothing here touches it
 * on an issue that already exists: two renderers taking turns on one block
 * would rewrite each other's wording on every run.
 */
export function matchBlock(job) {
  const v = job.llm || {};
  const mark = { blocker: '⛔', good: '✅' };
  const lines = [
    MATCH_OPEN,
    '',
    `**${job.employer || '?'}** — ${job.city || ''}, ${job.country || ''}`,
    '',
    `- **Deadline:** ${(job.deadline || 'not stated').slice(0, 10)}`
      + (job.days_left === null || job.days_left === undefined
        ? '' : ` (**${job.days_left} days left**)`),
    `- **Posted:** ${(job.posted || '').slice(0, 10)}`,
    `- **Type:** ${job.position_type}`,
    `- **Topic match:** ${job.score}/100 — ${job.tier_name || job.tier}`
      + ' (research content only; employer, country and supervisor score nothing)',
    `- **Advert:** ${job.url}`,
    '',
  ];

  const flags = job.flags || [];
  if (flags.length) {
    lines.push('**Context (not scored):** ' + flags.map(
      (f) => `${mark[f.severity] || '·'} ${f.name} (${(f.hits || []).join(', ')})`,
    ).join(' · '), '');
  }

  if (v.verdict) {
    lines.push(`### Gemini verdict: \`${v.verdict}\` — fit ${v.fit}/10`, '',
      `> ${v.headline || ''}`, '');
    if (v.group_or_supervisor) lines.push(`**Group / supervisor:** ${v.group_or_supervisor}`, '');
    for (const [heading, key] of [['Why it matches', 'why_match'],
      ['Why it might not', 'why_not'], ['Blockers', 'blockers'], ['To prepare', 'prep']]) {
      const items = v[key] || [];
      if (items.length) lines.push(`**${heading}**`, ...items.map((x) => `- ${x}`), '');
    }
  } else {
    lines.push('_No LLM verdict for this one (below threshold, or the call failed)._', '');
  }

  // Python prints a whole factor as "1.0"; keep that, so the block this
  // writes is byte-identical to the one the scan writes and the scan's first
  // pass over it is a no-op rather than a pointless PATCH.
  const factor = (f) => (Number.isInteger(f) ? `${f}.0` : `${f}`);
  const hits = (job.breakdown || []).filter((b) => (b.hits || []).length).map(
    (b) => `\`${b.label}\` ${b.applied >= 0 ? '+' : ''}${b.applied ?? b.weight}`
      + ` = ${b.weight >= 0 ? '+' : ''}${b.weight} × ${factor(b.factor ?? 1)}`
      + ` (${b.note || b.where || ''}): ${(b.hits || []).join(', ')}`,
  );
  if (hits.length) {
    lines.push('<details><summary>Topic match breakdown</summary>', '',
      ...hits.map((h) => `- ${h}`), '', '</details>', '');
  }

  lines.push(MATCH_CLOSE);
  return lines.join('\n');
}

const FOOTER = ['---',
  '**Decide by labelling this issue:** `decision:apply` · `decision:maybe` · '
    + '`decision:skip` · `decision:applied`',
  '',
  'Closing the issue without a decision label counts as `skip`. '
    + 'The dashboard refreshes a few minutes after you label.'].join('\n');

export async function ensureIssue({ jobId, job, block, cvSection, tierLabel }) {
  const existing = await findIssue(jobId);

  if (existing) {
    let body = withChecklist(existing.body || '', block);
    if (cvSection) body = withCvSection(body, cvSection);
    const updated = await gh('PATCH', `/issues/${existing.number}`, { body, state: 'open' });
    await addLabel(existing.number, 'decision:apply');
    return updated;
  }

  // The scan is not guaranteed to have been here first — with the fit gate it
  // usually has not — so write the whole skeleton, in the shape the scan
  // writes it, and let the scan keep the match block current from then on.
  const parts = [`${MARKER}${jobId} -->`, '', matchBlock(job), '', FOOTER, '', block];
  if (cvSection) parts.push('', cvSection);
  const title = `[${job.tier_name || job.tier}·${job.score}] ${job.title} — ${job.employer}`;
  const created = await gh('POST', '/issues', {
    title: title.slice(0, 250),
    body: `${parts.join('\n')}\n`,
    labels: ['radar', 'decision:apply', ...(tierLabel ? [tierLabel] : [])],
  });
  return created;
}

const CV_OPEN = '<!-- radar:cv -->';
const CV_CLOSE = '<!-- /radar:cv -->';

export function cvBlock(markdown, model) {
  return [
    CV_OPEN,
    '',
    '<details>',
    `<summary><b>针对本岗位定制的简历（${model} 生成，未经核对）</b></summary>`,
    '',
    markdown,
    '',
    '</details>',
    '',
    CV_CLOSE,
  ].join('\n');
}

export function withCvSection(body, block) {
  const start = body.indexOf(CV_OPEN);
  const end = body.indexOf(CV_CLOSE);
  if (start !== -1 && end > start) {
    return body.slice(0, start) + block + body.slice(end + CV_CLOSE.length);
  }
  return `${body.trimEnd()}\n\n${block}\n`;
}

/**
 * Write the CV section into an issue that already exists, leaving the
 * checklist and anything hand-written alone. The CV arrives minutes after the
 * checklist does, so it cannot ride along with ensureIssue.
 */
export async function updateIssueCv(number, cvSection) {
  const issue = await gh('GET', `/issues/${number}`);
  const body = withCvSection(issue.body || '', cvSection);
  if (body === (issue.body || '')) return issue;
  return gh('PATCH', `/issues/${number}`, { body });
}

export async function addLabel(number, label) {
  try {
    await gh('POST', `/issues/${number}/labels`, { labels: [label] });
  } catch {
    // A missing label definition is not worth failing the whole request over.
  }
}
