// GET /api/applications — every vacancy with preparation under way, so the
// dashboard can show progress without one request per card.
import { init, sql } from './_lib/db.mjs';
import { fail, json } from './_lib/http.mjs';

export default async function handler(req, res) {
  if (req.method !== 'GET') return fail(res, 405, 'GET only');
  try {
    await init();
    const rows = await sql()`
      select a.job_id, a.status, a.issue_number, a.issue_url, a.updated_at, a.error,
             count(m.key)                                    as total,
             count(*) filter (where m.status = 'done')       as done
        from application a
        left join material m on m.job_id = a.job_id
       group by a.job_id
       order by a.updated_at desc`;
    const applications = {};
    for (const r of rows) {
      applications[r.job_id] = {
        status: r.status,
        issue: r.issue_number,
        issue_url: r.issue_url,
        total: Number(r.total),
        done: Number(r.done),
        error: r.error || null,
        updated_at: r.updated_at,
      };
    }
    return json(res, 200, { applications });
  } catch (err) {
    return fail(res, 500, String(err.message || err));
  }
}
