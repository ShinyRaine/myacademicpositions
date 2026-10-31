"""Tests for the parts that got this wrong at least once during development.

Run: python -m unittest discover -s tests -v
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from radar import classify, parse, score  # noqa: E402

PROFILE = yaml.safe_load((Path(__file__).resolve().parent.parent / "profile.yaml").read_text())
NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


_UNUSED = """<!doctype html><html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"x"}</script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"Doctoral student in robotic manipulation","description":"Short summary.",
"datePosted":"2026-10-01T03:10:11+00:00","validThrough":"2026-11-30T22:59:59+00:00",
"hiringOrganization":{"@type":"Organization","name":"KTH Royal Institute of Technology"},
"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
"addressLocality":"Stockholm","addressCountry":"Sweden"}}}</script>
</head><body>
<div class="editor ck-content" id="editor">
<h3>Project description</h3><p>We work on <strong>learning from demonstration</strong>
and control barrier functions.</p><div class="note"><p>Nested block.</p></div>
<p>Apply by the deadline.</p>
</div>
<script>var junk = "should not appear";</script>
<footer>Site footer text</footer></body></html>"""


class TestPositionType(unittest.TestCase):
    def test_english_and_swedish_phd_titles(self):
        for title in ("PhD position in IC Design group",
                      "Doctoral student in robotic manipulation",
                      "Doktorander inom säkerhet i nätverkade system"):
            self.assertEqual(classify.position_type(title), "phd", title)

    def test_swedish_faculty_and_researcher_titles_are_not_phd(self):
        # Both of these were classified as PhD before the body fallback was
        # tightened, which put two faculty vacancies on the board.
        self.assertEqual(
            classify.position_type("Lektor i informationssystem för produktionsutveckling"),
            "professor")
        self.assertEqual(
            classify.position_type("Forskare i beräkningsbaserad magnetism och spinntronik"),
            "researcher")

    def test_body_fallback_ignores_a_mere_mention_of_phd_students(self):
        body = ("Ämnesområde Informationssystem. I anställningen ingår att "
                "handleda doktorander och undervisa på grundnivå.")
        self.assertEqual(classify.position_type("Tjänst vid skolan för ITM", body), "other")

    def test_body_fallback_fires_on_an_explicit_vacancy_phrase(self):
        body = "We are advertising a doctoral student position in autonomous systems."
        self.assertEqual(classify.position_type("Two openings in Robotics", body), "phd")

    def test_phd_beats_researcher_in_a_compound_title(self):
        self.assertEqual(classify.position_type("PhD researcher in robotics"), "phd")


class TestLanguage(unittest.TestCase):
    def test_detects_swedish(self):
        self.assertEqual(
            classify.language("Doktorander inom säkerhet i nätverkade system",
                              "Vi söker doktorander. Behörighet och anställning vid KTH."), "sv")

    def test_defaults_to_english(self):
        self.assertEqual(
            classify.language("Doctoral students in Networked Systems Security",
                              "We invite applications for doctoral student positions."), "en")


def _job(jid, title, lang, posted, deadline, employer="KTH", body="x"):
    return {"id": jid, "title": title, "language": lang, "posted": posted,
            "deadline": deadline, "employer": employer, "body": body,
            "url": f"https://x/ad/a/b/{jid}"}


class TestMergeTranslations(unittest.TestCase):
    def test_merges_an_en_sv_pair_posted_a_second_apart(self):
        jobs = [
            _job("253620", "Doctoral student in Game-Theoretic Methods", "en",
                 "2026-10-01T03:10:11+00:00", "2026-11-01T22:59:59+00:00"),
            _job("253629", "Doktorand inom spelteoretiska metoder", "sv",
                 "2026-10-01T03:10:12+00:00", "2026-11-01T22:59:59+00:00"),
        ]
        kept, merged = classify.merge_translations(jobs)
        self.assertEqual(merged, 1)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["id"], "253620")          # English survives
        self.assertEqual(kept[0]["also_posted_as"][0]["id"], "253629")

    def test_never_merges_two_adverts_in_the_same_language(self):
        # Two unrelated Ghent PhD projects share employer, deadline and posting
        # day. Keying on the date alone collapsed them into one.
        jobs = [
            _job("1", "PhD on composite hydrogen tanks", "en",
                 "2026-09-01T08:00:00+00:00", "2026-12-01T22:59:59+00:00", "Ghent University"),
            _job("2", "PhD on snap fit joints", "en",
                 "2026-09-01T08:00:00+00:00", "2026-12-01T22:59:59+00:00", "Ghent University"),
        ]
        kept, merged = classify.merge_translations(jobs)
        self.assertEqual(merged, 0)
        self.assertEqual(len(kept), 2)

    def test_pairs_each_translation_with_the_nearest_advert_id(self):
        jobs = [
            _job("253620", "Game-Theoretic Methods", "en",
                 "2026-10-01T03:10:11+00:00", "2026-11-01T22:59:59+00:00"),
            _job("253622", "Deformable objects", "en",
                 "2026-10-01T03:10:11+00:00", "2026-11-01T22:59:59+00:00"),
            _job("253631", "Deformerbara objekt", "sv",
                 "2026-10-01T03:10:12+00:00", "2026-11-01T22:59:59+00:00"),
        ]
        kept, merged = classify.merge_translations(jobs)
        self.assertEqual(merged, 1)
        self.assertEqual(len(kept), 2)
        paired = [j for j in kept if j.get("also_posted_as")]
        self.assertEqual(paired[0]["id"], "253622")   # nearest id, not the first


class TestScoring(unittest.TestCase):
    def _score(self, **kw):
        job = {"title": "", "summary": "", "body": "", "employer": "",
               "country": "", "deadline": None}
        job.update(kw)
        return score.score_job(job, PROFILE, NOW)

    def test_a_robotics_advert_outscores_an_off_topic_one_at_the_home_university(self):
        on_topic = self._score(
            title="Doctoral student in robot manipulation and motion planning",
            summary="learning from demonstration with dynamical systems",
            employer="ETH Zurich", country="Switzerland")
        off_topic = self._score(
            title="Doctoral student in thermomechanical fatigue of textile composites",
            employer="Chalmers University of Technology", country="Sweden")
        self.assertGreater(on_topic["score"], off_topic["score"])
        self.assertEqual(off_topic["score"], 0)

    def test_employer_country_and_supervisor_add_nothing_to_the_score(self):
        # The whole point of the rewrite: identical text, every contextual
        # advantage on one of them, same number.
        plain = self._score(title="PhD in robot learning and manipulation",
                            employer="Nowhere Polytechnic", country="Moldova")
        loaded = self._score(title="PhD in robot learning and manipulation",
                             body="Supervised by Jana Tumova in RPL. Fully funded, 4-year.",
                             employer="Chalmers University of Technology", country="Sweden")
        self.assertEqual(plain["score"], loaded["score"])
        self.assertEqual([b for b in loaded["breakdown"] if b["kind"] != "topic"], [])

    def test_context_still_travels_with_the_job_as_flags(self):
        job = self._score(title="PhD in robot learning",
                          body="Supervised by Jana Tumova. Fluency in Swedish is required.",
                          employer="Chalmers University of Technology", country="Sweden")
        flags = {f["label"]: f for f in job["flags"]}
        self.assertIn("named-supervisor", flags)
        self.assertIn("home", flags)
        self.assertEqual(flags["language-blocker"]["severity"], "blocker")

    def test_a_term_in_the_title_counts_for_more_than_one_buried_in_the_body(self):
        titled = self._score(title="Doctoral student in robot manipulation")
        buried = self._score(title="Doctoral student in nanocrystal spectroscopy",
                             body="our department also covers robotics and photonics")
        self.assertGreater(titled["score"], buried["score"] * 3)

    def test_specialised_vocabulary_needs_the_matching_field(self):
        # "Dynamical systems" and "formal methods" mean different subjects
        # outside robotics; they must not buy a full core group there.
        in_robotics = self._score(
            title="Doctoral student in formal methods for autonomous robots",
            summary="temporal logic specifications for mobile robots")
        elsewhere = self._score(
            title="Doctoral student in web and emerging applications security",
            summary="formal methods for sandboxing browser extensions")
        self.assertGreater(in_robotics["score"], 3 * elsewhere["score"])

    def test_plural_titles_match_singular_profile_terms(self):
        job = self._score(title="Doctoral student in formal methods for autonomous robots")
        self.assertIn("robotics", {b["label"] for b in job["breakdown"]})

    def test_hyphenation_does_not_change_the_match(self):
        a = self._score(title="PhD on dynamical system motion policies for robots")
        b = self._score(title="PhD on dynamical-system motion policies for robots")
        self.assertEqual(a["score"], b["score"])

    def test_exclusion_stems_match_their_full_words(self):
        # The list is written as stems; a closing word boundary made them inert.
        job = self._score(title="PhD in pharmacology and epidemiology of robots")
        off = [b for b in job["breakdown"] if b["label"] == "off-field"]
        self.assertTrue(off)
        self.assertIn("pharmac", off[0]["hits"])

    def test_exclusion_penalty_applies_from_the_title(self):
        job = self._score(title="PhD in molecular biology and proteomics of robots",
                          employer="Chalmers University of Technology", country="Sweden")
        labels = {d["label"] for d in job["breakdown"]}
        self.assertIn("off-field", labels)

    def test_days_left_and_urgency(self):
        job = self._score(title="PhD in robotics", deadline="2026-11-05T22:59:59+00:00")
        self.assertEqual(job["days_left"], 6)
        self.assertTrue(job["urgent"])
        self.assertFalse(job["expired"])

    def test_expired_advert_is_flagged_and_filtered(self):
        job = self._score(title="PhD in robotics", deadline="2026-10-01T22:59:59+00:00")
        job["position_type"] = "phd"
        self.assertTrue(job["expired"])
        ok, why = score.passes_hard_filters(job, PROFILE)
        self.assertFalse(ok)
        self.assertIn("deadline", why)

    def test_hard_filter_rejects_a_disallowed_position_type(self):
        job = self._score(title="Associate professor in robotics")
        job["position_type"] = "professor"
        ok, _ = score.passes_hard_filters(job, PROFILE)
        self.assertFalse(ok)


class TestTiers(unittest.TestCase):
    def test_score_carries_the_display_name_not_just_the_letter(self):
        job = score.score_job(
            {"title": "PhD in robot manipulation and task and motion planning",
             "summary": "", "body": "learning from demonstration",
             "employer": "KTH Royal Institute of Technology", "country": "Sweden",
             "deadline": None}, PROFILE, NOW)
        self.assertIn(job["tier"], score.TIER_KEYS)
        self.assertEqual(job["tier_name"], PROFILE["tiers"][job["tier"]]["name"])

    def test_tier_of_falls_through_to_the_bottom_band(self):
        key, name = score.tier_of(0, PROFILE)
        self.assertEqual(key, "D")
        self.assertEqual(name, PROFILE["tiers"]["D"]["name"])

    def test_tier_table_accepts_a_bare_threshold(self):
        table = score.tier_table({"tiers": {"A": 50, "B": 30, "C": 10, "D": 0}})
        self.assertEqual(table["A"]["min"], 50)
        self.assertEqual(table["A"]["name"], "A")
        self.assertIsNone(table["A"]["github"])

    def test_only_the_banded_tiers_get_a_github_label(self):
        table = score.tier_table(PROFILE)
        self.assertTrue(table["A"]["github"])
        self.assertTrue(table["B"]["github"])
        self.assertIsNone(table["C"]["github"])


class TestProfileIntegrity(unittest.TestCase):
    def test_tier_thresholds_descend(self):
        t = score.tier_table(PROFILE)
        self.assertGreater(t["A"]["min"], t["B"]["min"])
        self.assertGreater(t["B"]["min"], t["C"]["min"])

    def test_every_tier_has_a_readable_name(self):
        for key, entry in score.tier_table(PROFILE).items():
            self.assertTrue(entry["name"])
            self.assertNotEqual(entry["name"], key, f"tier {key} still shows its letter")

    def test_every_weighted_group_has_terms(self):
        for g in PROFILE["topics"]["groups"]:
            self.assertTrue(g.get("terms"), g)
            self.assertIn("weight", g)

    def test_every_context_flag_has_terms_and_a_readable_name(self):
        for f in PROFILE["context"]["flags"]:
            self.assertTrue(f.get("terms"), f)
            self.assertTrue(f.get("name"), f)
            self.assertNotIn("weight", f, f"{f['label']} must not score")

    def test_nothing_outside_topics_can_score(self):
        for key in ("employers", "countries", "bonuses"):
            self.assertNotIn(key, PROFILE, f"{key} is back in the score")

    def test_requires_points_at_a_real_group(self):
        labels = {g["label"] for g in PROFILE["topics"]["groups"]}
        for g in PROFILE["topics"]["groups"]:
            if g.get("requires"):
                self.assertIn(g["requires"], labels)

    def test_the_groups_can_reach_the_top_band(self):
        # A perfect advert must be able to clear tier A, or the band is dead.
        best = sum(g["weight"] for g in PROFILE["topics"]["groups"])
        self.assertGreaterEqual(min(best, PROFILE["topics"]["cap"]),
                                score.tier_table(PROFILE)["A"]["min"])


if __name__ == "__main__":
    unittest.main()
