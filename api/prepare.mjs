// POST /api/prepare { jobId }
//
// Stage one of two: decide to apply, and get the checklist. It reads the
// advert, asks Gemini what the advert actually demands, stores that so progress
// can be ticked off, and opens (or updates) the GitHub issue with it.
//
// The tailored CV used to be generated in this same request. It is not any
// more: it runs on a model that thinks for minutes (api/tailor.mjs), and
// waiting for it held the checklist — the part that is wanted immediately —
// hostage to it, inside a 60 s function. The dashboard calls the two in turn.
import { applicationOf, init, materialsOf, sql } from './_lib/db.mjs';
import { extractMaterials, CV_MODEL, DEFAULT_MODEL } from './_lib/gemini.mjs';
import { checklist, cvBlock, ensureIssue } from './_lib/github.mjs';
import { authorised, fail, json, readJson, siteFile } from './_lib/http.mjs';
import { PROFILE } from './_lib/profile.mjs';

export const config = { maxDuration: 60 };

export default async function handler(req, res) {
  if (req.method !== 'POST') return fail(res, 405, 'POST only');
  if (!authorised(req)) return fail(res, 401, 'bad or missing x-radar-key');

  let jobId;
  try {
    ({ jobId } = await readJson(req));
  } catch {
    return fail(res, 400, 'body must be JSON');
  }
  if (!jobId) return fail(res, 400, 'jobId is required');

  try {
    await init();

    // Already prepared: hand back what is stored rather than spending quota.
    const existing = await applicationOf(jobId);
    if (existing && existing.status === 'ready' && !req.query?.force) {
      return json(res, 200, {
        application: existing,
        materials: await materialsOf(jobId),
        reused: true,
      });
    }

    const [jobsFile, bodies] = await Promise.all([
      siteFile(req, 'jobs.json'),
      siteFile(req, 'bodies.json').catch(() => ({})),
    ]);
    const job = (jobsFile.jobs || []).find((j) => j.id === jobId);
    if (!job) return fail(res, 404, `no vacancy ${jobId} on the current board`);
    const body = bodies[jobId] || job.summary || '';

    const model = process.env.GEMINI_MODEL || DEFAULT_MODEL;
    // A CV already written for this vacancy survives a re-run of the checklist.
    const cvDone = existing?.cv_status === 'ready';

    await sql()`
      insert into application (job_id, title, employer, url, deadline, status, model, error, cv_status)
      values (${jobId}, ${job.title}, ${job.employer}, ${job.url},
              ${job.deadline || null}, 'preparing', ${model}, null, 'pending')
      on conflict (job_id) do update
        set status = 'preparing', model = ${model}, error = null,
            cv_status = coalesce(application.cv_status, 'pending'),
            updated_at = now()`;

    let extracted;
    try {
      extracted = await extractMaterials({ job, body, profile: PROFILE, model });
    } catch (err) {
      const message = String(err.message || err);
      await sql()`
        update application set status = 'failed', error = ${message}, updated_at = now()
         where job_id = ${jobId}`;
      return fail(res, 502, `could not read the advert's material list: ${message}`);
    }

    const { materials, how_to_submit, deadline_note } = extracted;

    // Replace the list wholesale, but keep the status and notes of any
    // material that survives a re-run.
    const previous = new Map((await materialsOf(jobId)).map((m) => [m.key, m]));
    await sql()`delete from material where job_id = ${jobId}`;
    for (const m of materials) {
      const old = previous.get(m.key);
      await sql()`
        insert into material (job_id, key, name, required, what, advice, status, notes, sort)
        values (${jobId}, ${m.key}, ${m.name}, ${m.required}, ${m.what}, ${m.advice},
                ${old?.status || 'todo'}, ${old?.notes || null}, ${m.sort})`;
    }

    const stored = await materialsOf(jobId);
    const block = checklist(stored, { how_to_submit, deadline_note });
    // Park a placeholder so the issue says what is still coming; /api/tailor
    // replaces exactly this block when the CV lands. An existing CV is left be.
    const cvSection = cvDone ? null : cvBlock(
      `_定制简历正在用 ${CV_MODEL} 生成，通常 1-5 分钟。_\n\n`
        + '_没出现就是那一步失败了，在面板上点「重新生成简历」。_',
      CV_MODEL,
    );

    let issue = null;
    let issueError = null;
    try {
      issue = await ensureIssue({ jobId, job, block, cvSection });
    } catch (err) {
      issueError = String(err.message || err);
    }

    await sql()`
      update application
         set status     = ${issueError ? 'partial' : 'ready'},
             how_to_submit = ${how_to_submit || null},
             deadline_note = ${deadline_note || null},
             issue_number = ${issue?.number || null},
             issue_url  = ${issue?.html_url || null},
             error      = ${issueError},
             updated_at = now()
       where job_id = ${jobId}`;

    return json(res, 200, {
      application: await applicationOf(jobId),
      materials: stored,
      how_to_submit,
      deadline_note,
      issue_error: issueError,
    });
  } catch (err) {
    const message = String(err.message || err);
    await sql()`
      update application set status = 'failed', error = ${message}, updated_at = now()
       where job_id = ${jobId}`.catch(() => {});
    return fail(res, 500, message);
  }
}
