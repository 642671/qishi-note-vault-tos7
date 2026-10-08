from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from qishi_note_vault.database import (
    Database,
    MAX_BODY_CHARS,
    MAX_TAG_CHARS,
    MAX_TAGS_PER_NOTE,
    MAX_TITLE_CHARS,
)


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "notes.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_create_update_search_and_tags(self) -> None:
        note = self.database.create_note(
            title="Python notes",
            body="Learn sqlite and markdown",
            tags=["Study", "Python"],
        )
        updated = self.database.update_note(
            note["id"],
            body="Learn sqlite, markdown, and tests",
            tags=["Study", "Python", "Testing"],
        )
        self.assertEqual(updated["tags"], ["Python", "Study", "Testing"])

        results = self.database.list_notes(query="sqlite")
        self.assertEqual([item["id"] for item in results], [note["id"]])
        tagged = self.database.list_notes(tag="Testing")
        self.assertEqual([item["id"] for item in tagged], [note["id"]])
        self.assertEqual(self.database.list_tags()[0]["name"], "Python")

    def test_pin_archive_and_trash_views(self) -> None:
        note = self.database.create_note(title="One", body="Body")
        self.database.update_note(note["id"], pinned=True)
        self.assertEqual(len(self.database.list_notes(view="pinned")), 1)

        self.database.update_note(note["id"], pinned=False, archived=True)
        self.assertEqual(self.database.list_notes(view="active"), [])
        self.assertEqual(len(self.database.list_notes(view="archived")), 1)

        self.database.update_note(note["id"], archived=False, trashed=True)
        self.assertEqual(len(self.database.list_notes(view="trash")), 1)

    def test_export_and_import(self) -> None:
        note = self.database.create_note(
            title="Backup",
            body="# Backup body",
            tags=["backup"],
        )
        payload = self.database.export_data()
        self.assertEqual(len(payload["notes"]), 1)

        second_dir = Path(self.temp.name) / "second"
        second = Database(second_dir / "notes.db")
        result = second.import_data(payload)
        self.assertEqual(result, {"imported": 1, "updated": 0})
        restored = second.get_note(note["id"])
        self.assertEqual(restored["body"], "# Backup body")
        self.assertEqual(restored["tags"], ["backup"])

    def test_input_boundaries(self) -> None:
        note = self.database.create_note(
            title="T" * (MAX_TITLE_CHARS + 1),
            body="Body",
            tags=[f"tag-{index}-" + "x" * 50 for index in range(MAX_TAGS_PER_NOTE + 5)],
        )
        self.assertEqual(len(note["title"]), MAX_TITLE_CHARS)
        self.assertEqual(len(note["tags"]), MAX_TAGS_PER_NOTE)
        self.assertTrue(all(len(tag) <= MAX_TAG_CHARS for tag in note["tags"]))

        with self.assertRaises(ValueError):
            self.database.create_note(
                title="Too long body",
                body="x" * (MAX_BODY_CHARS + 1),
            )

        one = self.database.create_note(title="Page limit", body="Body")
        self.assertEqual(
            [item["id"] for item in self.database.list_notes(limit=0)],
            [one["id"]],
        )


if __name__ == "__main__":
    unittest.main()
