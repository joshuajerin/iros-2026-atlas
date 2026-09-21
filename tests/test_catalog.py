import tempfile
import unittest
from pathlib import Path

from iros_catalog.catalog import backfill_official_details, import_official_records
from iros_catalog.db import connect, initialize
from iros_catalog.official import extract_official_paper_details, extract_official_records
from iros_catalog.enrich import _arxiv_candidate, _crossref_candidate, _exa_failure_detail, _exa_match, _exa_query, _fuzzy_query, _normalise_title
from iros_catalog.taxonomy import classify


class CatalogTests(unittest.TestCase):
    def test_extracts_embedded_index_data_and_ras_id(self):
        html = '''<script>[{"pn":"7","title":"A Robot Paper","authors":"Ada; Grace","keywords":"Manipulation","session":"Demo"}]</script><script>var PAPER_IDS = {"7":"123"};</script><script>{"7":"Example University; Example Lab"}</script>'''
        records = extract_official_records(html)
        self.assertEqual(records[0]["official_record_url"], "https://rasevents.org/presentation?id=123")
        self.assertEqual(records[0]["infovaya_record"], "https://rasevents.org/presentation?id=123")
        self.assertEqual(records[0]["affiliations"], "Example University; Example Lab")

    def test_import_normalizes_authors_and_topics(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "iros.sqlite"
            initialize(db)
            import_official_records(str(db), [{"pn":"7","title":"Diffusion Policies for Dexterous Grasping","authors":"Ada; Grace","keywords":"Learning; Manipulation","session":"Robot Learning","type":"Talk", "day":"Monday", "time":"9:00", "room":"101", "affiliations":"Example University; Example Lab", "official_record_url":"https://example.test/7"}])
            with connect(db) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM papers").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM paper_authors").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT affiliations FROM papers").fetchone()[0], "Example University; Example Lab")
                self.assertEqual(connection.execute("SELECT infovaya_record FROM papers").fetchone()[0], "https://example.test/7")
                self.assertEqual(connection.execute("SELECT author FROM papers").fetchone()[0], "Ada; Grace")
                self.assertEqual(connection.execute("SELECT keywords FROM papers").fetchone()[0], '["Learning", "Manipulation"]')
                topics = {row[0] for row in connection.execute("SELECT topic FROM paper_topics")}
                self.assertTrue({"ai_and_foundation_models", "manipulation_and_grasping"} <= topics)

    def test_backfill_uses_official_abstract_and_keywords(self):
        dataset = {"views": {"F": {"themes": {"Demo": [{"pn": "7", "abstract": "Official abstract.", "keywords": "Grasping; Perception"}]}}}}
        self.assertEqual(extract_official_paper_details(dataset), {"7": {"abstract": "Official abstract.", "keywords": "Grasping; Perception"}})
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "iros.sqlite"
            initialize(db)
            import_official_records(str(db), [{"pn": "7", "title": "A Robot Paper", "authors": "Ada", "keywords": "Old", "session": "Demo", "official_record_url": "https://example.test/7"}])
            counts = backfill_official_details(str(db), extract_official_paper_details(dataset))
            self.assertEqual(counts["abstracts_updated"], 1)
            with connect(db) as connection:
                row = connection.execute("SELECT abstract,abstract_source,official_keywords,keywords FROM papers").fetchone()
                self.assertEqual(tuple(row), ("Official abstract.", "official_iros_schedule", "Grasping; Perception", '["Grasping", "Perception"]'))

    def test_fallback_topic(self):
        self.assertEqual(classify("Unusual System", "", ""), ["other_robotics"])

    def test_extracts_arxiv_result(self):
        atom = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2510.19962v1</id><title> Configuration-Dependent Robot Kinematics Model and Calibration </title></entry></feed>'''
        self.assertEqual(_arxiv_candidate(atom), ("2510.19962", "Configuration-Dependent Robot Kinematics Model and Calibration"))

    def test_builds_a_distinctive_fuzzy_query(self):
        self.assertEqual(_fuzzy_query("ExplicitDP: Generalized Multi-Task Manipulation with Explicit Diffusion Policies"), "ti:explicitdp AND ti:generalized AND ti:multi AND ti:task")

    def test_normalises_title_punctuation_for_bulk_matching(self):
        self.assertEqual(_normalise_title("Robot, Map: A Test"), _normalise_title("Robot Map A Test"))

    def test_crossref_candidate_requires_exact_title_and_ieee_landing_page(self):
        items = [{"title": ["Robot, Map: A Test"], "DOI": "10.1109/example.123", "resource": {"primary": {"URL": "https://ieeexplore.ieee.org/document/123/"}}, "abstract": "<jats:p>Mapped &amp; indexed.</jats:p>"}]
        self.assertEqual(
            _crossref_candidate(items, "Robot Map A Test"),
            ("10.1109/example.123", "https://ieeexplore.ieee.org/document/123", "Mapped & indexed."),
        )

    def test_exa_candidate_requires_exact_title_and_never_uses_pdf(self):
        title = "A Robot, Map: A Test"
        candidate = _exa_match(title, [
            {"title": title, "url": "https://arxiv.org/pdf/2510.12345", "highlights": ["Abstract: Ignore this PDF."]},
            {"title": title, "url": "https://arxiv.org/html/2510.12345v2", "highlights": ["Abstract: Mapped & indexed.\nKeywords: robotics"]},
        ])
        self.assertEqual(candidate, ("https://arxiv.org/abs/2510.12345", "arxiv", "2510.12345", "Mapped & indexed."))

    def test_exa_extracts_markdown_abstract_heading(self):
        candidate = _exa_match("A Robot Paper", [{
            "title": "A Robot Paper",
            "url": "https://lab.example.edu/paper",
            "highlights": ["# A Robot Paper\n\n## Abstract\n\nA concise abstract.\n\n## Keywords\nRobotics"],
        }])
        self.assertEqual(candidate[-1], "A concise abstract.")

    def test_exa_query_includes_first_author_hint(self):
        self.assertIn("First author: Woolim Hong.", _exa_query("A Paper", "Woolim Hong"))

    def test_exa_failure_detail_preserves_query_and_candidates(self):
        detail = _exa_failure_detail("A Paper Ada", [{"title": "Candidate", "url": "https://example.test"}])
        self.assertIn("A Paper Ada", detail)
        self.assertIn("https://example.test", detail)

    def test_relaxed_exa_match_requires_author_and_title_terms(self):
        candidate = _exa_match("Efficient Powered Knee Prostheses", [{
            "title": "Lab publication",
            "url": "https://lab.example.edu/papers/knee",
            "text": "Woolim Hong developed efficient continuous impedance control for powered knee prostheses.",
        }], "Hong, Woolim", relaxed_author=True)
        self.assertEqual(candidate[1], "institutional_repository")
