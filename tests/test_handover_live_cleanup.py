"""The handover verifier must never delete state it failed to acquire."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_handover_brief_live import HandoverLiveVerifier  # noqa: E402


class HandoverVerifierCleanupTests(unittest.TestCase):
    def verifier_for(self, state_dir: Path, owns_state: bool) -> HandoverLiveVerifier:
        verifier = object.__new__(HandoverLiveVerifier)
        verifier.httpd = None
        verifier.http_thread = None
        verifier.app_stopped = False
        verifier.live = SimpleNamespace(stop=lambda: None)
        verifier.state_dir = state_dir
        verifier.state_dir_owned = owns_state
        verifier.owned_state_removed = False
        return verifier

    def test_preexisting_collision_state_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "collision"
            state_dir.mkdir()
            marker = state_dir / "do-not-delete.txt"
            marker.write_text("owned by someone else\n", encoding="utf-8")
            verifier = self.verifier_for(state_dir, owns_state=False)

            verifier.stop()

            self.assertEqual(marker.read_text(encoding="utf-8"), "owned by someone else\n")
            self.assertFalse(verifier.owned_state_removed)

    def test_run_owned_state_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "owned"
            state_dir.mkdir()
            (state_dir / "receipt.json").write_text("{}\n", encoding="utf-8")
            verifier = self.verifier_for(state_dir, owns_state=True)

            verifier.stop()

            self.assertFalse(state_dir.exists())
            self.assertTrue(verifier.owned_state_removed)


if __name__ == "__main__":
    unittest.main()
