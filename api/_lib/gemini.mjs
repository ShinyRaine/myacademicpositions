// Gemini calls for the application-preparation step.
//
// Two separate calls rather than one: the materials checklist is a small
// structured object and the tailored CV is long prose, and asking for both in
// one response makes the model shorten whichever it writes second. They do not
// depend on each other, so they run in parallel.

const API = 'https://generativelanguage.googleapis.com/v1beta/models';

export const DEFAULT_MODEL = 'gemini-3.5-flash';

function key() {
  const k = process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY;
  if (!k) throw new Error('GEMINI_API_KEY is not set on this deployment');
  return k;
}

async function call(model, { system, parts, schema, maxTokens = 4096, timeoutMs = 55_000 }) {
  const body = {
    contents: [{ role: 'user', parts }],
    generationConfig: {
      temperature: 0.2,
      maxOutputTokens: maxTokens,
      ...(schema ? { responseMimeType: 'application/json', responseSchema: schema } : {}),
    },
  };
  if (system) body.systemInstruction = { parts: [{ text: system }] };

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`${API}/${model}:generateContent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-goog-api-key': key() },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
  }

  const text = await res.text();
  if (!res.ok) {
    const hint = res.status === 429
      ? 'the free-tier daily quota is gone; try again after it resets'
      : text.slice(0, 300);
    throw new Error(`Gemini ${res.status}: ${hint}`);
  }

  const data = JSON.parse(text);
  const cand = data.candidates?.[0];
  const out = (cand?.content?.parts || []).map((p) => p.text || '').join('').trim();
  if (!out) throw new Error(`Gemini returned nothing (${cand?.finishReason || 'no reason given'})`);
  return out;
}

// ---------------------------------------------------------------- materials

const MATERIALS_SCHEMA = {
  type: 'object',
  properties: {
    materials: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          key: { type: 'string' },
          name: { type: 'string' },
          required: { type: 'boolean' },
          what: { type: 'string' },
          advice: { type: 'string' },
        },
        required: ['key', 'name', 'required', 'what', 'advice'],
      },
    },
    how_to_submit: { type: 'string' },
    deadline_note: { type: 'string' },
  },
  required: ['materials'],
};

const MATERIALS_SYSTEM = `You read one academic job advert and list exactly the
application materials it asks for.

List only what this advert actually asks for. Do not add the things academic
applications usually want — no "cover letter" unless the advert names one, no
"transcripts" unless the advert names them. If the advert is vague and only
says "your application", say so in one item named "Application" rather than
inventing a list.

For each material:
  key     a short lowercase slug, e.g. cv, cover_letter, transcripts,
          research_statement, references, publication_list, code_sample
  name    what the advert calls it, in English
  required true if the advert demands it, false if it says "optional",
          "preferably" or "an advantage"
  what    what the advert says this document must contain or prove, in one
          sentence. Quote the advert's own wording where it is specific
          (page limits, language, file format, naming conventions).
  advice  how this specific candidate should prepare it for this specific
          advert, in one or two sentences. Name the concrete thing from their
          background to put in it. Never give generic advice that would fit
          any applicant.

how_to_submit: the submission channel and any portal, reference number or file
naming rule the advert states. Empty string if the advert does not say.

deadline_note: anything the advert says about timing beyond the date itself
(rolling review, interviews starting earlier, start date). Empty if nothing.

Answer in English.`;

export async function extractMaterials({ job, body, profile, model = DEFAULT_MODEL }) {
  const prompt = `CANDIDATE (for the advice field)
${profile}

ADVERT
Title: ${job.title}
Employer: ${job.employer}
Location: ${job.city || ''}${job.country ? `, ${job.country}` : ''}
Application deadline: ${(job.deadline || 'not stated').slice(0, 10)}

Advert text:
"""
${(body || job.summary || '').slice(0, 14000)}
"""

List the application materials this advert asks for.`;

  const raw = await call(model, {
    system: MATERIALS_SYSTEM,
    parts: [{ text: prompt }],
    schema: MATERIALS_SCHEMA,
    maxTokens: 3072,
  });
  const parsed = JSON.parse(raw);
  const seen = new Set();
  const materials = (parsed.materials || [])
    .map((m, i) => ({
      key: String(m.key || `item_${i}`).toLowerCase().replace(/[^a-z0-9_]+/g, '_').slice(0, 40),
      name: String(m.name || '').slice(0, 120),
      required: m.required !== false,
      what: String(m.what || '').slice(0, 800),
      advice: String(m.advice || '').slice(0, 1200),
      sort: i,
    }))
    .filter((m) => m.name && !seen.has(m.key) && seen.add(m.key) !== false);

  return {
    materials,
    how_to_submit: String(parsed.how_to_submit || '').slice(0, 1000),
    deadline_note: String(parsed.deadline_note || '').slice(0, 600),
  };
}

// ---------------------------------------------------------------- tailor CV

const CV_SYSTEM = `You rewrite one candidate's CV for one specific advert.

You are working from their real CV. Every fact in your output must already be
in that CV — you may reorder, reword, foreground and cut, and you may say what
a listed project involved in different words, but you may not add an
achievement, a skill, a date, a publication or a role that is not there. If the
advert asks for something the CV does not show, do not quietly supply it: list
it under GAPS at the end instead.

Produce GitHub-flavoured Markdown:

## Tailored CV
The rewritten CV, in the order this advert would want to read it. Put the
experience the advert asks for first. Keep it to what fits on two pages.

## What changed and why
At most 6 bullets. Each names a change and the sentence in the advert that
motivated it.

## Gaps
What the advert asks for that this CV does not evidence, and — where it is
honestly possible — what in the candidate's background comes closest. Say
plainly if nothing does. Empty section if there are no gaps.

Write the CV in English.`;

export async function tailorCv({ job, body, profile, cv, model = DEFAULT_MODEL }) {
  const parts = [];
  if (cv) {
    // Gemini reads the PDF itself, which keeps the layout and the wording the
    // candidate actually wrote; extracting text first loses both.
    parts.push({ inlineData: { mimeType: cv.mime, data: cv.content_b64 } });
  }
  parts.push({
    text: `CANDIDATE NOTES (background, not a substitute for the CV above)
${profile}

ADVERT
Title: ${job.title}
Employer: ${job.employer}
Location: ${job.city || ''}${job.country ? `, ${job.country}` : ''}
Application deadline: ${(job.deadline || 'not stated').slice(0, 10)}

Advert text:
"""
${(body || job.summary || '').slice(0, 14000)}
"""

${cv ? 'Rewrite the attached CV for this advert.'
     : 'No CV file has been uploaded. Write the three sections from the candidate '
       + 'notes alone, and say in Gaps that the CV itself was not available.'}`,
  });

  return call(model, { system: CV_SYSTEM, parts, maxTokens: 6144 });
}
