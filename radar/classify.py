"""Position type, posting language, and English/Swedish duplicate detection.

The site's own position taxonomy only appears in site-wide navigation on an ad
page, not as the ad's own metadata, so the type is read off the title (which is
reliable: these are templated HR titles) with the body as a fallback.
"""
from __future__ import annotations

import re
import unicodedata

# Ordered: the first family that matches the title wins, so "PhD" beats the
# "researcher" in "PhD researcher".
_TYPES: list[tuple[str, tuple[str, ...]]] = [
    ("phd", (
        "phd", "ph.d", "doctoral student", "doctoral researcher", "doctoral position",
        "doctoral candidate", "doctorate", "doctorant", "doctorand", "doktorand",
        "doktorander", "doktorat", "promovend", "promovendus", "promotieplaats",
        "early stage researcher", "esr ", "dottorato", "predoctoral", "pre-doctoral",
    )),
    ("postdoc", (
        "postdoc", "post-doc", "post doc", "postdoctoral", "post-doctoral",
        "forskare efter", "postdoktor",
    )),
    ("research_assistant", (
        "research assistant", "research associate", "scientific assistant",
        "forskningsassistent", "wissenschaftliche", "research aide",
        "junior researcher", "research intern", "project assistant",
    )),
    ("professor", (
        "full professor", "associate professor", "assistant professor",
        "professor", "professorship", "professur", "chair in",
        "universitair docent", "tenure track", "tenure-track",
        "lecturer", "senior lecturer", "reader in", "docent",
        # Swedish: a "lektor" advert is a faculty post, not a studentship
        "lektor", "universitetslektor", "bitradande lektor", "adjunkt",
    )),
    ("researcher", (
        "researcher", "research fellow", "staff scientist", "scientist",
        "forskare", "forsker",
    )),
    ("engineer", ("research engineer", "engineer", "developer", "technician")),
    ("management", (
        "director", "head of", "manager", "coordinator", "dean", "administrator",
        "officer", "secretary",
    )),
]

# Swedish/German/Dutch/French/Italian/Spanish markers. Swedish ads are the ones
# that matter: KTH and Chalmers publish the same vacancy twice, EN and SV.
_NON_EN = {
    "sv": ("doktorand", "forskare", "tjänsten", "ansökan", "inom", "och ", "vid ",
           "arbetsuppgifter", "behörighet", "universitetslektor", "anställning"),
    "de": ("wissenschaftliche", "mitarbeiter", "bewerbung", "lehrstuhl", "fakultät",
           "promotion", "stelle", "universität"),
    "nl": ("promovendus", "onderzoeker", "vacature", "universiteit", "faculteit"),
    "fr": ("doctorant", "chercheur", "recherche sur", "université", "laboratoire de"),
    "it": ("dottorato", "ricercatore", "università", "borsa di"),
    "es": ("investigador", "doctorado", "universidad", "convocatoria"),
}

# Unambiguous phrases that announce the vacancy itself, not a mention of one.
_BODY_PHD = (
    "doctoral student position", "position as a doctoral student",
    "doctoral studentship", "phd student position", "phd position",
    "position as a phd", "we offer a phd", "we are offering a phd",
    "doctoral candidate position", "phd vacancy", "phd scholarship",
    "doctoral scholarship", "anstallning som doktorand",
    "doktorandanstallning", "utbildning pa forskarniva",
)
_BODY_RA = (
    "research assistant position", "position as a research assistant",
    "research associate position", "position as a research associate",
)


def fold(text: str) -> str:
    """Lower-case and strip diacritics.

    Both the advert and the profile go through this, so "ETH Zürich" matches
    "eth zurich" and "Linköping" matches "linkoping" without the profile having
    to spell every accented variant.
    """
    text = unicodedata.normalize("NFKD", text or "").lower()
    return "".join(c for c in text if not unicodedata.combining(c))


def _norm(text: str) -> str:
    return f" {re.sub(r'[^a-z0-9.+-]+', ' ', fold(text)).strip()} "


def position_type(title: str, body: str = "") -> str:
    """One of the keys in _TYPES, or 'other'."""
    t = _norm(title)
    for name, needles in _TYPES:
        if any(f" {n.strip()}" in t or t.startswith(f" {n.strip()}") for n in needles):
            return name
    # Titles like "Two positions in Robotics" carry the type in the body only.
    # This has to be strict: a lectureship advert says it involves supervising
    # "doktorander", which a loose keyword match reads as a PhD vacancy.
    head = _norm(body[:1500])
    if any(p in head for p in _BODY_PHD):
        return "phd"
    if any(p in head for p in _BODY_RA):
        return "research_assistant"
    return "other"


def language(title: str, body: str) -> str:
    """Rough posting language: 'en' unless a non-English marker set dominates."""
    blob = _norm(f"{title} {body[:2500]}")
    best, best_hits = "en", 1
    for lang, markers in _NON_EN.items():
        hits = sum(1 for m in markers if m in blob)
        if hits > best_hits:
            best, best_hits = lang, hits
    return best


def dedup_key(job: dict) -> tuple:
    """Group candidate translations of one vacancy.

    Measured on the KTH pairs 253620/253629 and 253622/253631: the English and
    Swedish versions of one vacancy share the employer and the deadline exactly
    and are written within a second or two of each other, so the key is the
    posting timestamp truncated to the minute. The posting *date* alone is far
    too coarse - it merged two unrelated Ghent PhD projects - and the exact
    second misses the real twins.
    """
    return (
        (job.get("employer") or "").strip().lower(),
        (job.get("deadline") or ""),
        (job.get("posted") or "")[:16],   # YYYY-MM-DDTHH:MM
    )


def prefer(a: dict, b: dict) -> dict:
    """Pick the version to keep: English first, then the longer body."""
    a_en = a.get("language", "en") == "en"
    b_en = b.get("language", "en") == "en"
    if a_en != b_en:
        return a if a_en else b
    return a if len(a.get("body") or "") >= len(b.get("body") or "") else b


def merge_translations(jobs: list[dict]) -> tuple[list[dict], int]:
    """Collapse each vacancy's language variants into one record.

    Only cross-language records are ever merged. Two adverts in the same
    language are always two vacancies, however identical their metadata, so a
    batch of PhD openings posted together survives intact.
    """
    groups: dict[tuple, list[dict]] = {}
    for job in jobs:
        groups.setdefault(dedup_key(job), []).append(job)

    kept, merged = [], 0
    for members in groups.values():
        if len(members) == 1:
            kept.append(members[0])
            continue

        by_lang: dict[str, list[dict]] = {}
        for job in members:
            by_lang.setdefault(job.get("language", "en"), []).append(job)

        if len(by_lang) == 1:  # all one language: distinct vacancies
            kept.extend(members)
            continue

        # Pair each translation with the nearest advert id in the main language,
        # which is how the site numbers the rows it writes together.
        main_lang = "en" if "en" in by_lang else max(by_lang, key=lambda k: len(by_lang[k]))
        primaries = by_lang.pop(main_lang)
        for job in primaries:
            kept.append(job)

        def num(j: dict) -> int:
            try:
                return int(j["id"])
            except (ValueError, KeyError):
                return 0

        for others in by_lang.values():
            for job in others:
                target = min(primaries, key=lambda p: abs(num(p) - num(job)))
                winner = prefer(target, job)
                loser = job if winner is target else target
                winner.setdefault("also_posted_as", []).append({
                    "id": loser["id"], "title": loser["title"],
                    "url": loser["url"], "language": loser.get("language"),
                })
                if winner is not target:  # translation won: swap it in
                    kept[kept.index(target)] = winner
                    primaries[primaries.index(target)] = winner
                merged += 1

    return kept, merged
