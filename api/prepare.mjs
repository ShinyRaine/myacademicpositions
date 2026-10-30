// POST /api/prepare { jobId }
//
// The one action the dashboard was missing: decide to apply, and get the work
// laid out. It reads the advert, asks Gemini for the material checklist and a
// CV tailored to this vacancy, stores the checklist so progress can be ticked
// off, and opens (or updates) the GitHub issue with both.
import { applicationOf, currentCv, init, materialsOf, sql } from './_lib/db.mjs';
import { extractMaterials, DEFAULT_MODEL, tailorCv } from './_lib/gemini.mjs';
import { checklist, cvBlock, ensureIssue } from './_lib/github.mjs';
import { authorised, fail, json, readJson, siteFile } from './_lib/http.mjs';

export const config = { maxDuration: 60 };

const PROFILE = `MSc student in Complex Adaptive Systems at Chalmers University of
Technology, Gothenburg (2025-2027). Master's thesis in Karinne Ramirez-Amaro's
group on neuro-symbolic task and motion planning: learning symbolic
preconditions and effects of manipulation actions from demonstrations, executed
with dynamical-system motion policies carrying stability and safety guarantees.
Hands-on with a real UR10e (ROS, RTDE, gripper and force/torque sensing),
Unity/AGX simulation, learning from demonstration, Gaussian-mixture DS fitting,
obstacle modulation, joint-space control barrier functions, admittance control
and passivity for physical human-robot interaction, and execution-time
condition monitoring with failure recovery. Available from June 2027. English
for research, Chinese native, no Swedish.`;

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

    const cv = await currentCv();
    const model = process.env.GEMINI_MODEL || DEFAULT_MODEL;

    await sql()`
      insert into application (job_id, title, employer, url, deadline, status, model, cv_id, error)
      values (${jobId}, ${job.title}, ${job.employer}, ${job.url},
              ${job.deadline || null}, 'preparing', ${model}, ${cv?.id || null}, null)
      on conflict (job_id) do update
        set status = 'preparing', model = ${model}, cv_id = ${cv?.id || null},
            error = null, updated_at = now()`;

    // Independent calls, so pay for one round trip rather than two.
    const [matsResult, cvResult] = await Promise.allSettled([
      extractMaterials({ job, body, profile: PROFILE, model }),
      tailorCv({ job, body, profile: PROFILE, cv, model }),
    ]);

    if (matsResult.status === 'rejected') {
      const message = String(matsResult.reason?.message || matsResult.reason);
      await sql()`
        update application set status = 'failed', error = ${message}, updated_at = now()
         where job_id = ${jobId}`;
      return fail(res, 502, `could not read the advert's material list: ${message}`);
    }

    const { materials, how_to_submit, deadline_note } = matsResult.value;

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
    const cvSection = cvResult.status === 'fulfilled'
      ? cvBlock(cvResult.value, model)
      : cvBlock(
        `_定制简历生成失败：${String(cvResult.reason?.message || cvResult.reason)}_\n\n`
          + '_材料清单不受影响。在面板上再点一次「重新生成」可以重试。_',
        model,
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
             error      = ${issueError
                            || (cvResult.status === 'rejected'
                              ? `CV: ${String(cvResult.reason?.message || cvResult.reason)}`
                              : null)},
             updated_at = now()
       where job_id = ${jobId}`;

    return json(res, 200, {
      application: await applicationOf(jobId),
      materials: stored,
      how_to_submit,
      deadline_note,
      cv_used: cv ? { filename: cv.filename, uploaded_at: cv.uploaded_at } : null,
      cv_failed: cvResult.status === 'rejected'
        ? String(cvResult.reason?.message || cvResult.reason) : null,
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
