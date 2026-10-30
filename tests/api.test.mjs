// Tests for the API logic that does not need a database.
// Run: node --test tests/
//
// The Neon driver speaks HTTP to a Neon endpoint, so the SQL path cannot be
// exercised locally; what is covered here is everything around it — the issue
// body surgery, the material normalisation, and the auth and body helpers.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  checklist, cvBlock, MARKER, withChecklist, withCvSection,
} from '../api/_lib/github.mjs';
import { authorised, readJson } from '../api/_lib/http.mjs';

const mats = [
  { key: 'cv', name: 'CV', required: true, what: 'Max two pages.', advice: 'Lead with the UR10e work.', status: 'done' },
  { key: 'cover_letter', name: 'Cover letter', required: true, what: 'One page.', advice: 'Name the group.', status: 'doing' },
  { key: 'publications', name: 'Publication list', required: false, what: '', advice: '', status: 'na' },
];

test('checklist ticks only what is done', () => {
  const out = checklist(mats);
  assert.match(out, /- \[x\] \*\*CV\*\*/);
  assert.match(out, /- \[ \] \*\*Cover letter\*\* — 进行中/);
  assert.match(out, /- \[ \] \*\*Publication list\*\* _\(可选\)_ — 不适用/);
});

test('checklist carries the advert wording and the advice', () => {
  const out = checklist(mats);
  assert.match(out, /广告要求：Max two pages\./);
  assert.match(out, /怎么准备：Lead with the UR10e work\./);
});

test('checklist says so when the advert lists nothing', () => {
  assert.match(checklist([]), /没有列出具体材料清单/);
});

test('checklist includes submission and timing notes when present', () => {
  const out = checklist(mats, { how_to_submit: 'Via the KTH portal.', deadline_note: 'Rolling review.' });
  assert.match(out, /\*\*投递方式：\*\* Via the KTH portal\./);
  assert.match(out, /\*\*时间说明：\*\* Rolling review\./);
});

test('withChecklist replaces only its own block and keeps the rest', () => {
  const body = `${MARKER}kth:1 -->\nSome scan-written preamble.\n\n`
    + `${checklist(mats)}\n\nA note I typed by hand.`;
  const updated = withChecklist(body, checklist([{ ...mats[0], status: 'todo' }]));
  assert.match(updated, /Some scan-written preamble\./);
  assert.match(updated, /A note I typed by hand\./);
  assert.match(updated, /- \[ \] \*\*CV\*\*/);
  assert.doesNotMatch(updated, /Cover letter/);
});

test('withChecklist appends when the issue has no block yet', () => {
  const updated = withChecklist('Body written by the scan.', checklist(mats));
  assert.match(updated, /Body written by the scan\./);
  assert.match(updated, /### 申请材料/);
});

test('a status change keeps the submission and timing notes', () => {
  // syncIssue rebuilds the block from stored values; if it forgets them the
  // submission channel silently disappears the first time a box is ticked.
  const extra = { how_to_submit: 'Via the KTH portal.', deadline_note: 'Rolling review.' };
  const body = withChecklist('head', checklist(mats, extra));
  const after = withChecklist(body, checklist([{ ...mats[0], status: 'todo' }], extra));
  assert.match(after, /\*\*投递方式：\*\* Via the KTH portal\./);
  assert.match(after, /\*\*时间说明：\*\* Rolling review\./);
});

test('withChecklist is idempotent', () => {
  const block = checklist(mats);
  const once = withChecklist('preamble', block);
  assert.equal(withChecklist(once, block), once);
});

test('the CV block is collapsed and replaceable', () => {
  const block = cvBlock('## Tailored CV\nline', 'gemini-3.5-flash');
  assert.match(block, /<details>/);
  assert.match(block, /gemini-3\.5-flash/);
  const body = withCvSection('preamble', block);
  const replaced = withCvSection(body, cvBlock('## Tailored CV\nsecond', 'gemini-3.5-flash'));
  assert.match(replaced, /second/);
  assert.doesNotMatch(replaced, /\nline\n/);
  assert.equal((replaced.match(/<details>/g) || []).length, 1);
});

test('checklist and CV blocks do not clobber each other', () => {
  let body = withChecklist('head', checklist(mats));
  body = withCvSection(body, cvBlock('cv one', 'm'));
  body = withChecklist(body, checklist([mats[0]]));
  assert.match(body, /cv one/);
  assert.match(body, /- \[x\] \*\*CV\*\*/);
  assert.doesNotMatch(body, /Cover letter/);
});

// ------------------------------------------------------------------ auth

test('writes are open when no key is configured', () => {
  delete process.env.RADAR_API_KEY;
  assert.equal(authorised({ headers: {} }), true);
});

test('a configured key is required and must match', () => {
  process.env.RADAR_API_KEY = 'swordfish';
  try {
    assert.equal(authorised({ headers: {} }), false);
    assert.equal(authorised({ headers: { 'x-radar-key': 'swordfis' } }), false);
    assert.equal(authorised({ headers: { 'x-radar-key': 'swordfish!' } }), false);
    assert.equal(authorised({ headers: { 'x-radar-key': 'swordfish' } }), true);
  } finally {
    delete process.env.RADAR_API_KEY;
  }
});

// ------------------------------------------------------------------ body

function fakeReq(chunks, extra = {}) {
  return {
    headers: {},
    ...extra,
    async* [Symbol.asyncIterator]() { for (const c of chunks) yield Buffer.from(c); },
  };
}

test('readJson parses a streamed body', async () => {
  assert.deepEqual(await readJson(fakeReq(['{"jobId":', '"kth:1"}'])), { jobId: 'kth:1' });
});

test('readJson accepts a body the platform already parsed', async () => {
  assert.deepEqual(await readJson(fakeReq([], { body: { jobId: 'x' } })), { jobId: 'x' });
  assert.deepEqual(await readJson(fakeReq([], { body: '{"jobId":"y"}' })), { jobId: 'y' });
});

test('an empty body is an empty object, not a crash', async () => {
  assert.deepEqual(await readJson(fakeReq([])), {});
});
