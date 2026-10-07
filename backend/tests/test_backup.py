from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from qishi_note_vault.notes import NoteService


class BackupTests(unittest.TestCase):
    def test_attachment_and_zip_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = NoteService(Path(first_dir))
            note = first.create_note({"title": "Attachment test", "body": "Body", "tags": ["files"]})
            attachment = first.save_attachment(
                note_id=note["id"],
                filename="sample.png",
                content_type="image/png",
                data=b"\x89PNG\r\n\x1a\nsample",
            )
            backup = first.export_backup()
            self.assertGreater(len(backup), 20)

            second = NoteService(Path(second_dir))
            result = second.import_backup(backup)
            self.assertEqual(result["imported"], 1)
            imported = second.get_note(note["id"])
            self.assertEqual(imported["title"], "Attachment test")
            self.assertEqual(len(imported["attachments"]), 1)
            record, data = second.read_attachment(attachment["id"])
            self.assertEqual(record["filename"], "sample.png")
            self.assertEqual(data, b"\x89PNG\r\n\x1a\nsample")


if __name__ == "__main__":
    unittest.main()
