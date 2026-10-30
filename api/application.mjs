// GET  /api/application?jobId=...   the checklist for one vacancy
// PATCH /api/application            update one material's status or notes
import { applicationOf, init, materialsOf, sql } from './_lib/db.mjs';
import { authorised, fail, json, readJson } from './_lib/http.mjs';
import { checklist, withChecklist } from './_lib/github.mjs';

const STATUSES = new Set(['todo', 'doing', 'done', 'na']);

async function syncIssue(jobId) {
  // Keep the issue's checklist in step with the dashboard. Only the block
  // between the markers is rewritten, so notes added in the issue survive.
  const app = await applicationOf(jobId);
  if (!app?.issue_number || !process.env.GITHUB_TOKEN) return;
  const materials = await materialsOf(jobId);
  const repo = process.env.GITHUB_REPOSITORY;
  const headers = {
    Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'Content-Type': 'application/json',
    'User-Agent': 'position-radar',
  };
  const cur = await fetch(`https://api.github.com/repos/${repo}/issues/${app.issue_number}`, { headers });
  if (!cur.ok) return;
  const issue = await cur.json();
  const body = withChecklist(issue.body || '', checklist(materials, {
    how_to_submit: app.how_to_submit,
    deadline_note: app.deadline_note,
  }));
  if (body === issue.body) return;
  await fetch(`https://api.github.com/repos/${repo}/issues/${app.issue_number}`, {
    method: 'PATCH', headers, body: JSON.stringify({ body }),
  });
}

export default async function handler(req, res) {
  try {
    await init();

    if (req.method === 'GET') {
      const jobId = (req.query?.jobId || '').toString();
      if (!jobId) return fail(res, 400, 'jobId is required');
      const application = await applicationOf(jobId);
      if (!application) return json(res, 200, { application: null, materials: [] });
      return json(res, 200, { application, materials: await materialsOf(jobId) });
    }

    if (req.method === 'PATCH') {
      if (!authorised(req)) return fail(res, 401, 'bad or missing x-radar-key');
      const { jobId, key, status, notes } = await readJson(req);
      if (!jobId || !key) return fail(res, 400, 'jobId and key are required');
      if (status !== undefined && !STATUSES.has(status)) {
        return fail(res, 400, `status must be one of ${[...STATUSES].join(', ')}`);
      }
      const q = sql();
      await q`
        update material
           set status     = coalesce(${status ?? null}, status),
               notes      = coalesce(${notes ?? null}, notes),
               updated_at = now()
         where job_id = ${jobId} and key = ${key}`;
      await q`update application set updated_at = now() where job_id = ${jobId}`;
      await syncIssue(jobId).catch(() => {});
      return json(res, 200, { materials: await materialsOf(jobId) });
    }

    return fail(res, 405, 'GET or PATCH only');
  } catch (err) {
    return fail(res, 500, String(err.message || err));
  }
}
