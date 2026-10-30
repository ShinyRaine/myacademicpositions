// GET  /api/cv  — what base CV is on file
// POST /api/cv  — upload one (raw body; filename and type in headers)
//
// The file is kept as base64 in Postgres rather than in blob storage: a CV is
// a few hundred kilobytes, it is sent to Gemini inline anyway, and this way the
// deployment needs one storage integration instead of two.
import { currentCv, init, sql } from './_lib/db.mjs';
import { authorised, fail, json, readBody } from './_lib/http.mjs';

const ALLOWED = new Set([
  'application/pdf',
  'text/plain',
  'text/markdown',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
]);
const MAX_BYTES = 8 * 1024 * 1024;

export default async function handler(req, res) {
  try {
    await init();

    if (req.method === 'GET') {
      const cv = await currentCv();
      if (!cv) return json(res, 200, { cv: null });
      return json(res, 200, {
        cv: {
          id: cv.id,
          filename: cv.filename,
          mime: cv.mime,
          size_bytes: cv.size_bytes,
          uploaded_at: cv.uploaded_at,
        },
      });
    }

    if (req.method === 'POST') {
      if (!authorised(req)) return fail(res, 401, 'bad or missing x-radar-key');

      const mime = String(req.headers['x-cv-type'] || req.headers['content-type'] || '')
        .split(';')[0].trim();
      if (!ALLOWED.has(mime)) {
        return fail(res, 415, `unsupported type ${mime || '(none)'}`,
          { allowed: [...ALLOWED] });
      }

      const filename = decodeURIComponent(String(req.headers['x-cv-name'] || 'cv'))
        .replace(/[^\w.() \-一-鿿]/g, '_').slice(0, 160);

      let raw;
      try {
        raw = await readBody(req, MAX_BYTES);
      } catch {
        return fail(res, 413, `the file is larger than ${MAX_BYTES / 1024 / 1024} MB`);
      }
      const buf = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
      if (!buf.length) return fail(res, 400, 'empty upload');

      const rows = await sql()`
        insert into cv (filename, mime, content_b64, size_bytes)
        values (${filename}, ${mime}, ${buf.toString('base64')}, ${buf.length})
        returning id, filename, mime, size_bytes, uploaded_at`;

      // Keep a short history rather than an unbounded one.
      await sql()`
        delete from cv where id not in (select id from cv order by uploaded_at desc limit 5)`;

      return json(res, 201, { cv: rows[0] });
    }

    return fail(res, 405, 'GET or POST only');
  } catch (err) {
    return fail(res, 500, String(err.message || err));
  }
}
