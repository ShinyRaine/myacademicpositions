// Request helpers shared by the endpoints.

export function json(res, status, payload) {
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.status(status).send(JSON.stringify(payload));
}

export function fail(res, status, message, extra = {}) {
  json(res, status, { error: message, ...extra });
}

/**
 * Write access check.
 *
 * Off by default, as asked: with no RADAR_API_KEY set on the deployment,
 * anyone who has the URL can trigger a run. Setting that one environment
 * variable turns the check on without a code change.
 */
export function authorised(req) {
  const expected = process.env.RADAR_API_KEY;
  if (!expected) return true;
  const given = req.headers['x-radar-key'] || '';
  return typeof given === 'string'
    && given.length === expected.length
    && given === expected;
}

export async function readBody(req, limitBytes = 12 * 1024 * 1024) {
  if (req.body !== undefined && req.body !== null) return req.body;
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limitBytes) throw new Error('request body too large');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

export async function readJson(req) {
  const raw = await readBody(req);
  if (Buffer.isBuffer(raw)) {
    if (!raw.length) return {};
    return JSON.parse(raw.toString('utf-8'));
  }
  if (typeof raw === 'string') return raw ? JSON.parse(raw) : {};
  return raw;
}

/** The deployment's own static files — jobs.json, bodies.json, profile text. */
export async function siteFile(req, path) {
  const host = req.headers['x-forwarded-host'] || req.headers.host;
  const proto = req.headers['x-forwarded-proto'] || 'https';
  const res = await fetch(`${proto}://${host}/${path}`, { headers: { 'Cache-Control': 'no-store' } });
  if (!res.ok) throw new Error(`could not read /${path} from this deployment (${res.status})`);
  return res.json();
}
