from pathlib import Path
import unittest


class ResearchBoundaryDocsTest(unittest.TestCase):
    def test_readme_separates_delivery_demo_from_thesis_evidence(self) -> None:
        text = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn("separate software-delivery control-plane demonstrator", text)
        self.assertIn("MUST NOT be pooled", text)
        self.assertIn("agent-smell-degradation/v1", text)
