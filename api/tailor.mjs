// POST /api/tailor { jobId }   write the tailored CV for one vacancy
// GET  /api/tailor?jobId=...   read back the stored text
//
// Stage two of preparing an application. This is a separate request from
// /api/prepare for one reason: the CV is written by gemini-3.8-flash, which
// thinks for 50 s to 5 minutes per call. The checklist model answers in
// seconds, and nothing is gained by making the person wait for both at once —
// or by capping the CV at the 60 s the checklist request runs under.
import { applicationOf, cvTextOf, currentCv, init, sql } from './_lib/db.mjs';
import { CV_MODEL, tailorCv } from './_lib/gemini.mjs';
import { cvBlock, findIssue, updateIssueCv } from './_lib/github.mjs';
import { authorised, fail, json, readJson, siteFile } from './_lib/http.mjs';
import { PROFILE } from './_lib/profile.mjs';

// Hobby functions run 300 s with fluid compute; the model call gives up at 280
// so there is time left to record why rather than being killed mid-thought.
export const config = { maxDuration: 300 };

/** Put the CV into this vacancy's issue, leaving the checklist alone. */
async function pushToIssue(jobId, app, markdown) {
  const number = app.issue_number || (await findIssue(jobId))?.number;
  if (!number) throw new Error('this vacancy has no radar issue yet');
  await updateIssueCv(number, cvBlock(markdown, CV_MODEL));
}

export default async function handler(req, res) {
  try {
    await init();

    if (req.method === 'GET') {
      // Guarded like a write, unlike the rest of the read endpoints: what comes
      // back is the CV itself, not a vacancy's public details.
      if (!authorised(req)) return fail(res, 401, 'bad or missing x-radar-key');
      const jobId = (req.query?.jobId || '').toString();
      if (!jobId) return fail(res, 400, 'jobId is required');
      const stored = await cvTextOf(jobId);
      return json(res, 200, { jobId, cv: stored });
    }

    if (req.method !== 'POST') return fail(res, 405, 'GET or POST only');
    if (!authorised(req)) return fail(res, 401, 'bad or missing x-radar-key');

    let jobId;
    try {
      ({ jobId } = await readJson(req));
    } catch {
      return fail(res, 400, 'body must be JSON');
    }
    if (!jobId) return fail(res, 400, 'jobId is required');
    const force = Boolean(req.query?.force);

    const app = await applicationOf(jobId);
    if (!app) return fail(res, 409, 'no checklist for this vacancy yet; run /api/prepare first');

    // Five minutes of model time is not spent twice over without being asked.
    if (app.cv_status === 'ready' && !force) {
      return json(res, 200, { application: app, model: app.cv_model, reused: true });
    }

    const [jobsFile, bodies] = await Promise.all([
      siteFile(req, 'jobs.json'),
      siteFile(req, 'bodies.json').catch(() => ({})),
    ]);
    const job = (jobsFile.jobs || []).find((j) => j.id === jobId);
    if (!job) return fail(res, 404, `no vacancy ${jobId} on the current board`);
    const body = bodies[jobId] || job.summary || '';
    const cv = await currentCv();

    await sql()`
      update application
         set cv_status = 'running', cv_model = ${CV_MODEL}, cv_error = null,
             cv_id = ${cv?.id || null}, updated_at = now()
       where job_id = ${jobId}`;

    let markdown;
    try {
      markdown = await tailorCv({ job, body, profile: PROFILE, cv, model: CV_MODEL });
    } catch (err) {
      const message = String(err.message || err);
      await sql()`
        update application
           set cv_status = 'failed', cv_error = ${message},
               cv_updated_at = now(), updated_at = now()
         where job_id = ${jobId}`;
      // Say so in the issue too, so the placeholder does not sit there for ever.
      await pushToIssue(jobId, app,
        `_定制简历生成失败：${message}_\n\n_材料清单不受影响。在面板上点「重新生成简历」可以重试。_`)
        .catch(() => {});
      return fail(res, 502, `could not write the tailored CV: ${message}`);
    }

    await sql()`
      update application
         set cv_status = 'ready', cv_markdown = ${markdown}, cv_error = null,
             cv_updated_at = now(), updated_at = now()
       where job_id = ${jobId}`;

    // The text is in the database by now, so a GitHub failure costs a retry of
    // the write, not of the model call.
    let issueError = null;
    try {
      await pushToIssue(jobId, app, markdown);
    } catch (err) {
      issueError = String(err.message || err);
      await sql()`
        update application set cv_error = ${`Issue: ${issueError}`}, updated_at = now()
         where job_id = ${jobId}`;
    }

    return json(res, 200, {
      application: await applicationOf(jobId),
      model: CV_MODEL,
      cv_used: cv ? { filename: cv.filename, uploaded_at: cv.uploaded_at } : null,
      issue_error: issueError,
    });
  } catch (err) {
    return fail(res, 500, String(err.message || err));
  }
}
