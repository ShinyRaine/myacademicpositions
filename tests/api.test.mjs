// Tests for the API logic that does not need a database.
// Run: node --test tests/
//
// The Neon driver speaks HTTP to a Neon endpoint, so the SQL path cannot be
// exercised locally; what is covered here is everything around it — the issue
// body surgery, the material normalisation, and the auth and body helpers.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import {
  checklist, cvBlock, MARKER, matchBlock, updateIssueCv, withChecklist, withCvSection,
} from '../api/_lib/github.mjs';
import { CV_MODEL, DEFAULT_MODEL, tailorCv } from '../api/_lib/gemini.mjs';
import { authorised, readJson } from '../api/_lib/http.mjs';

/** Swap in a fake fetch for one call and always put the real one back. */
async function withFetch(fake, fn) {
  const real = globalThis.fetch;
  globalThis.fetch = fake;
  try {
    return await fn();
  } finally {
    globalThis.fetch = real;
  }
}

const reply = (payload, status = 200) => new Response(JSON.stringify(payload), { status });

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

// -------------------------------------------------------------- the CV call

test('the CV runs on a different model from the checklist', () => {
  // If these ever become the same string, one of the two calls is paying the
  // other's latency: the checklist request has 60 s, the CV request has 300.
  assert.notEqual(CV_MODEL, DEFAULT_MODEL);
});

const job = { title: 'PhD in robotics', employer: 'KTH', deadline: '2026-11-30' };

test('the CV call leaves maxOutputTokens unset and asks for thinking', async () => {
  process.env.GEMINI_API_KEY = 'test-key';
  let sent = null;
  const out = await withFetch(async (url, opts) => {
    sent = { url, body: JSON.parse(opts.body) };
    return reply({ candidates: [{ content: { parts: [{ text: '## Tailored CV\nreal' }] } }] });
  }, () => tailorCv({ job, body: 'advert text', profile: 'candidate', cv: null }));

  assert.match(sent.url, new RegExp(`${CV_MODEL.replace('.', '\\.')}:generateContent`));
  // Thinking tokens come out of this budget, so capping it truncates the answer
  // before it starts.
  assert.equal(sent.body.generationConfig.maxOutputTokens, undefined);
  assert.ok(['minimal', 'low', 'medium', 'high'].includes(
    sent.body.generationConfig.thinkingConfig.thinkingLevel,
  ));
  assert.match(out, /Tailored CV/);
});

test('a model that rejects thinkingConfig is asked again without it', async () => {
  process.env.GEMINI_API_KEY = 'test-key';
  const bodies = [];
  const out = await withFetch(async (url, opts) => {
    bodies.push(JSON.parse(opts.body));
    return bodies.length === 1
      ? reply({ error: { message: 'Thinking level is not supported for this model' } }, 400)
      : reply({ candidates: [{ content: { parts: [{ text: 'plain answer' }] } }] });
  }, () => tailorCv({ job, body: 'advert text', profile: 'candidate', cv: null }));

  assert.equal(bodies.length, 2);
  assert.ok(bodies[0].generationConfig.thinkingConfig);
  assert.equal(bodies[1].generationConfig.thinkingConfig, undefined);
  assert.equal(out, 'plain answer');
});

test('a 503 from an overloaded model is retried, a 429 is not', async () => {
  process.env.GEMINI_API_KEY = 'test-key';
  let calls = 0;
  const out = await withFetch(async () => {
    calls += 1;
    return calls === 1
      ? reply({ error: { message: 'This model is currently experiencing high demand.' } }, 503)
      : reply({ candidates: [{ content: { parts: [{ text: 'second try' }] } }] });
  }, () => tailorCv({ job, body: 'advert text', profile: 'candidate', cv: null }));
  assert.equal(calls, 2);
  assert.equal(out, 'second try');

  let quotaCalls = 0;
  await withFetch(async () => {
    quotaCalls += 1;
    return reply({ error: { message: 'quota' } }, 429);
  }, async () => {
    await assert.rejects(
      tailorCv({ job, body: 'advert', profile: 'candidate', cv: null }),
      /429.*quota/s,
    );
  });
  assert.equal(quotaCalls, 1);
});

test('updateIssueCv replaces the placeholder and keeps the checklist', async () => {
  process.env.GITHUB_REPOSITORY = 'me/radar';
  process.env.GITHUB_TOKEN = 'test-token';
  const before = withCvSection(
    withChecklist('scan preamble', checklist(mats)),
    cvBlock('_定制简历正在生成…_', CV_MODEL),
  );
  try {
    const updated = await withFetch(async (url, opts) => (
      (opts.method || 'GET') === 'GET'
        ? reply({ number: 7, body: before })
        : reply({ number: 7, body: JSON.parse(opts.body).body })
    ), () => updateIssueCv(7, cvBlock('## Tailored CV\nthe real thing', CV_MODEL)));

    assert.match(updated.body, /the real thing/);
    assert.doesNotMatch(updated.body, /正在生成/);
    assert.match(updated.body, /- \[x\] \*\*CV\*\*/);
    assert.match(updated.body, /scan preamble/);
  } finally {
    delete process.env.GITHUB_REPOSITORY;
    delete process.env.GITHUB_TOKEN;
  }
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

/* ------------------------------------------------------- the match block */
// One issue per vacancy, written by two sides: the scan (radar/issues.py) and
// the dashboard (api/_lib/github.mjs). The dashboard opens most of them now,
// so what it writes has to be what the scan would have written — otherwise
// the scan's refresh pass rewrites every issue on every run.
test('matchBlock renders exactly what radar/issues.py renders', () => {
  const root = new URL('..', import.meta.url).pathname;
  const board = JSON.parse(readFileSync(`${root}/data/jobs.json`, 'utf8')).jobs;
  // A judged one, an unjudged one, one with flags and one with a penalty.
  const sample = [
    board.find((j) => j.llm),
    board.find((j) => !j.llm && (j.flags || []).length),
    board.find((j) => (j.breakdown || []).some((b) => b.label === 'off-field')),
    board[board.length - 1],
  ].filter(Boolean);

  const expected = execFileSync('python3', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(root)})
from radar import issues
ids = json.load(sys.stdin)
board = {j["id"]: j for j in json.load(open(${JSON.stringify(`${root}/data/jobs.json`)}))["jobs"]}
print("\\n#####\\n".join(issues.match_block(board[i]) for i in ids), end="")
`], { input: JSON.stringify(sample.map((j) => j.id)), encoding: 'utf8' });

  assert.equal(sample.map(matchBlock).join('\n#####\n'), expected);
});

test('the dashboard opens an issue the scan can keep updating', () => {
  const job = { id: 'ap:1', employer: 'ETH', city: 'Zurich', country: 'Switzerland',
    score: 41, tier: 'B', tier_name: '值得细看', url: 'u', breakdown: [], flags: [] };
  const block = matchBlock(job);
  assert.ok(block.startsWith('<!-- radar:match -->'));
  assert.ok(block.endsWith('<!-- /radar:match -->'));
  // The checklist block must survive a later rewrite of the match block.
  const body = `${MARKER}ap:1 -->\n\n${block}\n\n${checklist(mats)}`;
  assert.ok(withChecklist(body, checklist(mats)).includes('<!-- radar:match -->'));
});
