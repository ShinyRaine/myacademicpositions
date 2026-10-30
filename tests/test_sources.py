"""Source adapters, against saved fragments of the real pages."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import classify, parse, sources  # noqa: E402
from radar.sources import academic_positions as ap  # noqa: E402
from radar.sources import chalmers, kth  # noqa: E402


class TestRegistry(unittest.TestCase):
    def test_known_sources(self):
        self.assertEqual(sorted(sources.REGISTRY), ["ap", "chalmers", "kth"])

    def test_ids_are_namespaced(self):
        self.assertEqual(sources.key("kth", "973983"), "kth:973983")

    def test_active_respects_profile_order(self):
        mods = sources.active({"sources": ["kth", "ap"]})
        self.assertEqual([m.NAME for m in mods], ["kth", "ap"])

    def test_unknown_source_is_loud(self):
        with self.assertRaises(KeyError):
            sources.active({"sources": ["nope"]})


class TestDates(unittest.TestCase):
    def test_kth_dotted_month(self):
        self.assertTrue(parse.iso("30.Oct.2026").startswith("2026-10-30"))

    def test_chalmers_iso_and_prose(self):
        self.assertTrue(parse.iso("2026-11-27").startswith("2026-11-27"))
        self.assertTrue(parse.iso("November 27, 2026").startswith("2026-11-27"))

    def test_offset_timestamp_becomes_utc(self):
        self.assertEqual(parse.iso("2026-10-01T03:10:11+02:00")[:13], "2026-10-01T01")

    def test_garbage_is_none(self):
        self.assertIsNone(parse.iso("whenever"))
        self.assertIsNone(parse.iso(""))


KTH_AD = """<html><body>
<h1>Doctoral student in robotic manipulation of deformable objects</h1>
<div class="job-details-content">
  <h2>Project description</h2><p>Work within <b>GEOMANIP</b>, an ERC Starting Grant.</p>
  <div><p>Nested paragraph.</p></div>
</div>
<div class="details-list"><b>Type of employment</b><p>Temporary position</p></div>
<div class="details-list"><b>Location</b><p>Stockholm</p></div>
<div class="details-list"><b>Country</b><p>Sweden</p></div>
<div class="details-list"><b>Published</b><p>01.Oct.2026</p></div>
<div class="details-list"><b>Last application date</b><p>30.Oct.2026</p></div>
<div class="details-list"><b>Contact</b><div><div>Noemie Jaquier</div>
<a href="mailto:jaquier@kth.se">jaquier@kth.se</a></div></div>
<script>var junk = "hidden";</script>
</body></html>"""


class TestKTH(unittest.TestCase):
    def setUp(self):
        # fetch() makes the request itself, so drive its parsing over a saved
        # fragment instead.
        page = KTH_AD
        m = kth._H1.search(page)
        d = kth._details(page)
        body = parse.clean_text(parse.block(page, kth._BODY))
        self.rec = parse.record(
            id="kth:973983", url=kth.ad_url("973983"), source="kth",
            title=parse.clean_text(m.group(1)).strip(), body=body,
            employer=kth.EMPLOYER, city=d.get("location", ""), country=d.get("country", ""),
            posted=parse.iso(d.get("published")),
            deadline=parse.iso(d.get("last application date")),
            employment_type=d.get("type of employment", ""), contact=d.get("contact", ""))

    def test_reads_the_details_list_pairs(self):
        self.assertEqual(self.rec["city"], "Stockholm")
        self.assertEqual(self.rec["country"], "Sweden")
        self.assertEqual(self.rec["employment_type"], "Temporary position")
        self.assertTrue(self.rec["deadline"].startswith("2026-10-30"))
        self.assertTrue(self.rec["posted"].startswith("2026-10-01"))

    def test_keeps_the_hiring_contact(self):
        self.assertIn("Jaquier", self.rec["contact"])
        self.assertIn("jaquier@kth.se", self.rec["contact"])

    def test_body_survives_nesting_and_drops_scripts(self):
        self.assertIn("GEOMANIP", self.rec["body"])
        self.assertIn("Nested paragraph.", self.rec["body"])
        self.assertNotIn("hidden", self.rec["body"])

    def test_employer_matches_the_profile_spelling(self):
        # Scoring looks up "kth royal institute"; a different spelling here
        # would silently lose 22 points.
        self.assertIn("kth royal institute", self.rec["employer"].lower())

    def test_ad_url_is_the_english_page(self):
        self.assertTrue(kth.ad_url("1").endswith("?l=en"))


CHALMERS_LIST = """<table><tr><th>x</th></tr>
<tr> <td>20260514</td>
 <td><a href='https://web103.reachmee.com/ext/I003/304/job?site=5&amp;job_id=15156'
   class='btn btn-link'>Doctoral student in Web and Emerging Applications Security</a> </td>
 <td><span class='show-mobile'>Application deadline:</span>
   <span data-order="1" style="display:none">2026-11-27</span> 27/11/2026</td>
 <td>Computer Science and Engineering</td> <td>PhD Student Positions</td> </tr>
<tr> <td>20260501</td>
 <td><a href='https://web103.reachmee.com/ext/I003/304/job?site=5&amp;job_id=15137'
   class='btn btn-link'>Postdoc in Computational Physics</a> </td>
 <td><span class='show-mobile'>Application deadline:</span>
   <span data-order="1" style="display:none">2026-11-30</span> 30/11/2026</td>
 <td>Fysik och Astronomi</td> <td>Post Doctoral Positions</td> </tr>
</table>"""


class TestChalmersList(unittest.TestCase):
    def setUp(self):
        import re
        self.rows = []
        for row in re.split(r"<tr\b", CHALMERS_LIST, flags=re.I)[1:]:
            m = chalmers._JOB_ID.search(row)
            if not m:
                continue
            cells = [parse.clean_text(c).strip() for c in chalmers._CELL.findall(row)]
            t = chalmers._TITLE.search(row)
            d = chalmers._DEADLINE.search(row)
            self.rows.append({
                "id": m.group(1),
                "title": parse.clean_text(t.group(1)).strip() if t else "",
                "deadline": d.group(1) if d else None,
                "department": cells[-2] if len(cells) >= 2 else "",
                "position_hint": chalmers.CATEGORY.get(cells[-1].lower(), "") if cells else "",
            })

    def test_one_row_per_advert(self):
        self.assertEqual([r["id"] for r in self.rows], ["15156", "15137"])

    def test_row_carries_deadline_department_and_type(self):
        r = self.rows[0]
        self.assertEqual(r["title"], "Doctoral student in Web and Emerging Applications Security")
        self.assertEqual(r["deadline"], "2026-11-27")
        self.assertEqual(r["department"], "Computer Science and Engineering")
        self.assertEqual(r["position_hint"], "phd")

    def test_postdoc_category_is_mapped_so_it_can_be_filtered_unfetched(self):
        self.assertEqual(self.rows[1]["position_hint"], "postdoc")

    def test_employer_matches_the_profile_spelling(self):
        self.assertIn("chalmers", chalmers.EMPLOYER.lower())


class TestAcademicPositions(unittest.TestCase):
    def test_sitemap_rewrites_host_and_keeps_only_ads(self):
        xml = """<urlset>
          <url><loc>https://academicpositions.com/ad/kth/2026/a-job/12345</loc></url>
          <url><loc>https://academicpositions.com/employer/kth</loc></url>
        </urlset>"""
        self.assertEqual(ap.sitemap_urls(xml),
                         ["https://en.academicpositions.se/ad/kth/2026/a-job/12345"])

    def test_job_id_is_the_trailing_number(self):
        self.assertEqual(ap.job_id("https://x/ad/kth/2026/a-job/12345"), "12345")


def _job(jid, title, source, employer="KTH Royal Institute of Technology", **kw):
    base = {"id": jid, "title": title, "source": source, "employer": employer,
            "url": f"https://{source}/x/{jid}", "summary": "", "deadline": None,
            "contact": "", "body": "b"}
    base.update(kw)
    return base


class TestCrossSourceMerge(unittest.TestCase):
    PRIORITY = ["ap", "chalmers", "kth"]

    def test_the_universitys_own_record_wins_over_the_aggregator(self):
        jobs = [
            _job("ap:253622", "Doctoral student in robotic manipulation of deformable objects",
                 "ap", summary="Short blurb.", deadline="2026-10-30T22:59:59+00:00"),
            _job("kth:973983", "Doctoral student in robotic manipulation of deformable objects",
                 "kth", contact="Noemie Jaquier"),
        ]
        kept, merged = classify.merge_sources(jobs, self.PRIORITY)
        self.assertEqual(merged, 1)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["source"], "kth")
        self.assertEqual(kept[0]["contact"], "Noemie Jaquier")

    def test_fields_the_winner_lacks_are_carried_over(self):
        jobs = [
            _job("ap:1", "Doctoral student in X", "ap",
                 summary="Short blurb.", deadline="2026-12-01T22:59:59+00:00"),
            _job("kth:2", "Doctoral student in X", "kth"),
        ]
        kept, _ = classify.merge_sources(jobs, self.PRIORITY)
        self.assertEqual(kept[0]["summary"], "Short blurb.")
        self.assertEqual(kept[0]["deadline"], "2026-12-01T22:59:59+00:00")
        self.assertEqual(kept[0]["also_listed_on"][0]["source"], "ap")

    def test_different_vacancies_at_one_employer_are_not_merged(self):
        jobs = [
            _job("kth:1", "Doctoral student in robotics", "kth"),
            _job("kth:2", "Doctoral student in fluid mechanics", "kth"),
        ]
        kept, merged = classify.merge_sources(jobs, self.PRIORITY)
        self.assertEqual(merged, 0)
        self.assertEqual(len(kept), 2)

    def test_same_title_at_different_employers_is_not_merged(self):
        jobs = [
            _job("kth:1", "Doctoral student in robotics", "kth"),
            _job("chalmers:1", "Doctoral student in robotics", "chalmers",
                 employer="Chalmers University of Technology"),
        ]
        kept, merged = classify.merge_sources(jobs, self.PRIORITY)
        self.assertEqual(merged, 0)
        self.assertEqual(len(kept), 2)

    def test_never_merges_two_records_from_the_same_source(self):
        # Graz posts four separate vacancies under one generic title, with four
        # different deadlines. Merging on title alone destroyed three of them.
        jobs = [
            _job("ap:250090", "Universitatsassistent:in ohne Doktorat", "ap",
                 employer="University of Graz", deadline="2026-10-31T22:59:59+00:00"),
            _job("ap:254432", "Universitatsassistent:in ohne Doktorat", "ap",
                 employer="University of Graz", deadline="2026-11-13T22:59:59+00:00"),
            _job("ap:254585", "Universitatsassistent:in ohne Doktorat", "ap",
                 employer="University of Graz", deadline="2026-11-20T22:59:59+00:00"),
        ]
        kept, merged = classify.merge_sources(jobs, self.PRIORITY)
        self.assertEqual(merged, 0)
        self.assertEqual(len(kept), 3)

    def test_does_not_guess_when_a_source_lists_the_title_twice(self):
        jobs = [
            _job("ap:1", "Doctoral student in X", "ap"),
            _job("ap:2", "Doctoral student in X", "ap"),
            _job("kth:3", "Doctoral student in X", "kth"),
        ]
        kept, merged = classify.merge_sources(jobs, self.PRIORITY)
        self.assertEqual(merged, 0)
        self.assertEqual(len(kept), 3)

    def test_matching_ignores_case_and_punctuation(self):
        jobs = [
            _job("ap:1", "Doctoral students in Networked Systems Security", "ap"),
            _job("kth:2", "Doctoral  students in networked systems security!", "kth"),
        ]
        kept, merged = classify.merge_sources(jobs, self.PRIORITY)
        self.assertEqual(merged, 1)
        self.assertEqual(kept[0]["source"], "kth")


if __name__ == "__main__":
    unittest.main()
