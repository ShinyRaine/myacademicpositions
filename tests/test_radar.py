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


AD_PAGE = """<!doctype html><html><head>
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


class TestSitemap(unittest.TestCase):
    def test_rewrites_host_and_keeps_only_ads(self):
        xml = """<urlset>
          <url><loc>https://academicpositions.com/ad/kth/2026/a-job/12345</loc></url>
          <url><loc>https://academicpositions.com/employer/kth</loc></url>
          <url><loc>https://academicpositions.com/ad/eth/2026/b-job/999</loc></url>
        </urlset>"""
        urls = parse.sitemap_urls(xml)
        self.assertEqual(urls, [
            "https://en.academicpositions.se/ad/kth/2026/a-job/12345",
            "https://en.academicpositions.se/ad/eth/2026/b-job/999",
        ])

    def test_job_id_is_trailing_number(self):
        self.assertEqual(
            parse.job_id("https://en.academicpositions.se/ad/kth/2026/a-job/12345"), "12345")


class TestParseAd(unittest.TestCase):
    def setUp(self):
        self.rec = parse.parse_ad(
            "https://en.academicpositions.se/ad/kth/2026/a-job/12345", AD_PAGE)

    def test_picks_the_jobposting_block_not_the_organization(self):
        self.assertEqual(self.rec["title"], "Doctoral student in robotic manipulation")
        self.assertEqual(self.rec["employer"], "KTH Royal Institute of Technology")
        self.assertEqual(self.rec["country"], "Sweden")

    def test_body_is_the_editor_div_with_nesting_and_without_scripts(self):
        body = self.rec["body"]
        self.assertIn("learning from demonstration", body)
        self.assertIn("Nested block.", body)        # balanced <div> walk
        self.assertIn("Apply by the deadline.", body)
        self.assertNotIn("should not appear", body)  # script stripped
        self.assertNotIn("Site footer text", body)   # stops at the closing div

    def test_dates_are_normalised_to_utc(self):
        self.assertTrue(self.rec["deadline"].startswith("2026-11-30T"))
        self.assertIn("+00:00", self.rec["posted"])

    def test_returns_none_without_a_jobposting(self):
        self.assertIsNone(parse.parse_ad("https://x/ad/a/b/1", "<html><body>nothing</body></html>"))


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

    def test_folds_diacritics_when_matching_employers(self):
        # "ETH Zürich" did not match the profile's "eth zurich" before folding.
        hit = self._score(title="PhD in robot learning and manipulation",
                          employer="ETH Zürich", country="Switzerland")
        labels = {d["label"] for d in hit["breakdown"]}
        self.assertIn("euro-robotics", labels)

    def test_topic_gate_scales_down_an_off_topic_advert_at_a_weighted_employer(self):
        on_topic = self._score(
            title="Doctoral student in robot manipulation and motion planning",
            body="learning from demonstration, control barrier functions",
            employer="Chalmers University of Technology", country="Sweden")
        off_topic = self._score(
            title="Doctoral student in thermomechanical fatigue of textile composites",
            employer="Chalmers University of Technology", country="Sweden")
        self.assertGreater(on_topic["score"], off_topic["score"])
        # The off-topic one must not survive on employer weight alone.
        self.assertLess(off_topic["score"], PROFILE["tiers"]["C"]["min"])

    def test_topic_relevance_outranks_institution_weight(self):
        abroad = self._score(
            title="PhD in robot learning, manipulation and imitation learning",
            body="task and motion planning, dynamical systems, control barrier functions",
            employer="ETH Zürich", country="Switzerland")
        local_off_topic = self._score(
            title="Doctoral student in perovskite materials for hydrogen production",
            employer="Chalmers University of Technology", country="Sweden")
        self.assertGreater(abroad["score"], local_off_topic["score"])

    def test_named_supervisor_is_not_gated(self):
        job = self._score(title="Doctoral student in applied mathematics",
                          body="The supervisor is Jana Tumova.",
                          employer="KTH Royal Institute of Technology", country="Sweden")
        bonus = [d for d in job["breakdown"] if d["label"] == "named-supervisor"]
        self.assertEqual(bonus[0]["factor"], 1.0)

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
        groups = (PROFILE["topics"]["groups"] + PROFILE["employers"]["tiers"]
                  + PROFILE["countries"]["tiers"] + PROFILE["bonuses"]["signals"])
        for g in groups:
            self.assertTrue(g.get("terms"), g)
            self.assertIn("weight", g)

    def test_ungated_labels_exist_in_the_bonus_table(self):
        labels = {g.get("label") for g in PROFILE["bonuses"]["signals"]}
        for name in PROFILE["scoring"]["ungated_labels"]:
            self.assertIn(name, labels)


if __name__ == "__main__":
    unittest.main()
