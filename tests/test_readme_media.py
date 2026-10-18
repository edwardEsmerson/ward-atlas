"""Negative media/link/query controls use disposable files, never product media."""
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.readme_media_dependencies import image_module
from scripts.verify_readme_media import ACCEPTED_QUERY_SOURCE, MAX_IMAGE_BYTES, MediaError, references, verify

ROOT = Path(__file__).resolve().parents[1]


class ReadmeMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Image = image_module()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "docs/media").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        shutil.copyfile(ROOT / ACCEPTED_QUERY_SOURCE, self.root / ACCEPTED_QUERY_SOURCE)
        import ast
        source = ast.parse((ROOT / ACCEPTED_QUERY_SOURCE).read_text())
        query = next(n for n in source.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "CONVEYOR_PATH_QUERY" for t in n.targets))
        self.query = query.value.func.value.value.strip()  # literal triple-quoted string.strip()
        for i in range(8):
            self.Image.new("RGB", (12, 8), (10 + i, 25, 40)).save(self.root / f"docs/media/{i}.png")
        self.text = "# Test\n" + "\n".join(f"![Workflow {i}](docs/media/{i}.png)" for i in range(8))
        self.text += f"\n## Why a graph\n```cypher\n{self.query}\n```\n## Start with your FalkorDB server\n"
        self.write()

    def write(self):
        (self.root / "README.md").write_text(self.text)

    def reject(self, message):
        self.write()
        with self.assertRaisesRegex(MediaError, message):
            verify(self.root)

    def test_valid(self):
        report = verify(self.root)
        self.assertEqual(report["image_count"], 8)
        self.assertEqual(report["opening_screenshots"], 8)

    def test_missing_image(self):
        (self.root / "docs/media/0.png").unlink()
        self.reject("Missing image")

    def test_oversized_image(self):
        with (self.root / "docs/media/0.png").open("ab") as out:
            out.write(b"0" * MAX_IMAGE_BYTES)
        self.reject("Oversized image")

    def test_broken_link(self):
        self.text += "\n[missing guide](docs/no-such-guide.md)"
        self.reject("broken relative link")

    def test_broken_anchor(self):
        self.text += "\n[missing section](README.md#not-a-heading)"
        self.reject("Broken relative link anchor")

    def test_valid_anchor(self):
        self.text += "\n[query](#why-a-graph)"
        self.write()
        self.assertEqual(verify(self.root)["status"], "PASS")

    def test_missing_cypher(self):
        self.text = self.text.replace("```cypher", "```text")
        self.reject("Missing opening fenced cypher")

    def test_forbidden_sentence(self):
        for sentence in ("Human contributions have not been verified.", "AI-only entries may be rejected.",
                         "Contributions have **not been verified**."):
            with self.subTest(sentence=sentence):
                original = self.text
                self.text += "\n" + sentence
                self.reject("Forbidden sentence")
                self.text = original

    def test_corrupt_image(self):
        (self.root / "docs/media/0.png").write_bytes(b"not a PNG")
        self.reject("does not decode")

    def test_wrong_query(self):
        self.text = self.text.replace("owns.current = true", "owns.current = false")
        self.reject("not verbatim")

    def test_only_five_opening_images(self):
        for i in range(5, 8):
            self.text = self.text.replace(f"![Workflow {i}](docs/media/{i}.png)", "")
        self.reject("At least 6 distinct")

    def test_reference_and_html_images(self):
        self.text = self.text.replace("![Workflow 0](docs/media/0.png)", '![Workflow 0][zero]\n[zero]: docs/media/0.png')
        self.text = self.text.replace("![Workflow 1](docs/media/1.png)", '<img src="docs/media/1.png" alt="Workflow 1">')
        self.write()
        self.assertEqual(verify(self.root)["opening_screenshots"], 8)

    def test_code_is_not_a_link(self):
        self.assertEqual(references('```sh\n[not a link](no-file)\n```\n`[also not](absent)`'), [])


if __name__ == "__main__":
    unittest.main()
