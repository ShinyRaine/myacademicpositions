// Neon Postgres over HTTP. The serverless driver is used instead of `pg`
// because every request here is a cold-ish serverless invocation, and a TCP
// pool that outlives the invocation is what exhausts a Neon free-tier database.
import { neon } from '@neondatabase/serverless';

// The Vercel Neon integration injects several of these; take whichever exists.
const URL_VARS = [
  'DATABASE_URL', 'POSTGRES_URL', 'POSTGRES_PRISMA_URL',
  'NEON_DATABASE_URL', 'DATABASE_URL_UNPOOLED',
];

let _sql = null;

export function sql() {
  if (_sql) return _sql;
  const name = URL_VARS.find((v) => process.env[v]);
  if (!name) {
    throw new Error(
      `no database URL in the environment (looked for ${URL_VARS.join(', ')}). ` +
      'Add a Neon database under the Vercel project Storage tab.',
    );
  }
  _sql = neon(process.env[name]);
  return _sql;
}

let ready = false;

/** Create the tables on first use, so there is no migration step to forget. */
export async function init() {
  if (ready) return;
  const q = sql();
  await q`
    create table if not exists cv (
      id          serial primary key,
      filename    text        not null,
      mime        text        not null,
      content_b64 text        not null,
      size_bytes  integer     not null,
      uploaded_at timestamptz not null default now()
    )`;
  await q`
    create table if not exists application (
      job_id       text primary key,
      title        text,
      employer     text,
      url          text,
      deadline     timestamptz,
      issue_number integer,
      issue_url    text,
      status       text        not null default 'preparing',
      cv_id        integer     references cv(id),
      model        text,
      error        text,
      how_to_submit text,
      deadline_note text,
      created_at   timestamptz not null default now(),
      updated_at   timestamptz not null default now()
    )`;
  await q`
    create table if not exists material (
      job_id     text        not null references application(job_id) on delete cascade,
      key        text        not null,
      name       text        not null,
      required   boolean     not null default true,
      what       text,
      advice     text,
      status     text        not null default 'todo',
      notes      text,
      sort       integer     not null default 0,
      updated_at timestamptz not null default now(),
      primary key (job_id, key)
    )`;
  // Added after the first deploy; harmless when they already exist.
  await q`alter table application add column if not exists how_to_submit text`;
  await q`alter table application add column if not exists deadline_note text`;
  ready = true;
}

export async function currentCv() {
  const rows = await sql()`
    select id, filename, mime, content_b64, size_bytes, uploaded_at
      from cv order by uploaded_at desc limit 1`;
  return rows[0] || null;
}

export async function materialsOf(jobId) {
  return sql()`
    select key, name, required, what, advice, status, notes, sort, updated_at
      from material where job_id = ${jobId} order by sort, key`;
}

export async function applicationOf(jobId) {
  const rows = await sql()`
    select job_id, title, employer, url, deadline, issue_number, issue_url,
           status, model, error, how_to_submit, deadline_note, created_at, updated_at
      from application where job_id = ${jobId}`;
  return rows[0] || null;
}
