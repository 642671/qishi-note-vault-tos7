"""SQLite persistence for Qishi Note Vault."""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


class NoteNotFoundError(LookupError):
    """Raised when a requested note does not exist."""


class Database:
    """Small SQLite repository with one connection per operation."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    trashed INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE
                );

                CREATE TABLE IF NOT EXISTS note_tags (
                    note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    PRIMARY KEY (note_id, tag_id)
                );

                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    stored_name TEXT NOT NULL UNIQUE,
                    content_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_notes_updated
                    ON notes(trashed, archived, pinned, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_note_tags_tag
                    ON note_tags(tag_id, note_id);
                CREATE INDEX IF NOT EXISTS idx_attachments_note
                    ON attachments(note_id, created_at DESC);
                """
            )

    @staticmethod
    def normalize_tags(tags: Iterable[Any] | None) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in tags or []:
            name = str(value).strip()[:40]
            key = name.casefold()
            if not name or key in seen:
                continue
            seen.add(key)
            normalized.append(name)
        return normalized[:20]

    @staticmethod
    def _tag_names(connection: sqlite3.Connection, note_id: str) -> list[str]:
        rows = connection.execute(
            """
            SELECT tags.name
            FROM tags
            JOIN note_tags ON note_tags.tag_id = tags.id
            WHERE note_tags.note_id = ?
            ORDER BY tags.name COLLATE NOCASE
            """,
            (note_id,),
        ).fetchall()
        return [str(row["name"]) for row in rows]

    def _row_to_note(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "body": row["body"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "pinned": bool(row["pinned"]),
            "archived": bool(row["archived"]),
            "trashed": bool(row["trashed"]),
            "tags": self._tag_names(connection, str(row["id"])),
        }

    def _set_tags(self, connection: sqlite3.Connection, note_id: str, tags: Iterable[Any] | None) -> None:
        connection.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
        for name in self.normalize_tags(tags):
            connection.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
            row = connection.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
            if row is not None:
                connection.execute(
                    "INSERT OR IGNORE INTO note_tags(note_id, tag_id) VALUES (?, ?)",
                    (note_id, int(row["id"])),
                )
        connection.execute(
            """
            DELETE FROM tags
            WHERE id NOT IN (SELECT DISTINCT tag_id FROM note_tags)
            """
        )

    def create_note(
        self,
        *,
        title: str = "",
        body: str = "",
        tags: Iterable[Any] | None = None,
    ) -> dict[str, Any]:
        note_id = str(uuid.uuid4())
        now = int(time.time())
        clean_title = str(title).strip()[:200] or "Untitled"
        clean_body = str(body)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO notes(id, title, body, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (note_id, clean_title, clean_body, now, now),
            )
            self._set_tags(connection, note_id, tags)
            row = connection.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            assert row is not None
            return self._row_to_note(connection, row)

    def get_note(self, note_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise NoteNotFoundError(note_id)
            return self._row_to_note(connection, row)

    def update_note(
        self,
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        tags: Iterable[Any] | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        trashed: bool | None = None,
    ) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise NoteNotFoundError(note_id)

            next_title = row["title"] if title is None else (str(title).strip()[:200] or "Untitled")
            next_body = row["body"] if body is None else str(body)
            next_pinned = int(row["pinned"] if pinned is None else bool(pinned))
            next_archived = int(row["archived"] if archived is None else bool(archived))
            next_trashed = int(row["trashed"] if trashed is None else bool(trashed))
            connection.execute(
                """
                UPDATE notes
                SET title = ?, body = ?, updated_at = ?, pinned = ?, archived = ?, trashed = ?
                WHERE id = ?
                """,
                (
                    next_title,
                    next_body,
                    int(time.time()),
                    next_pinned,
                    next_archived,
                    next_trashed,
                    note_id,
                ),
            )
            if tags is not None:
                self._set_tags(connection, note_id, tags)
            updated = connection.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            assert updated is not None
            return self._row_to_note(connection, updated)

    def list_notes(
        self,
        *,
        query: str = "",
        tag: str = "",
        view: str = "active",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where = ["1 = 1"]
        parameters: list[Any] = []
        if view == "trash":
            where.append("notes.trashed = 1")
        elif view == "archived":
            where.append("notes.trashed = 0 AND notes.archived = 1")
        elif view == "pinned":
            where.append("notes.trashed = 0 AND notes.archived = 0 AND notes.pinned = 1")
        else:
            where.append("notes.trashed = 0 AND notes.archived = 0")

        clean_query = str(query).strip()
        if clean_query:
            where.append("(notes.title LIKE ? COLLATE NOCASE OR notes.body LIKE ? COLLATE NOCASE)")
            wildcard = f"%{clean_query}%"
            parameters.extend([wildcard, wildcard])

        clean_tag = str(tag).strip()
        if clean_tag:
            where.append(
                """
                EXISTS (
                    SELECT 1
                    FROM note_tags
                    JOIN tags ON tags.id = note_tags.tag_id
                    WHERE note_tags.note_id = notes.id AND tags.name = ? COLLATE NOCASE
                )
                """
            )
            parameters.append(clean_tag)

        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))
        parameters.extend([safe_limit, safe_offset])
        sql = f"""
            SELECT notes.*
            FROM notes
            WHERE {' AND '.join(where)}
            ORDER BY notes.pinned DESC, notes.updated_at DESC
            LIMIT ? OFFSET ?
        """
        with self.connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
            return [self._row_to_note(connection, row) for row in rows]

    def list_tags(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT tags.name, COUNT(notes.id) AS count
                FROM tags
                LEFT JOIN note_tags ON note_tags.tag_id = tags.id
                LEFT JOIN notes ON notes.id = note_tags.note_id AND notes.trashed = 0
                GROUP BY tags.id, tags.name
                HAVING COUNT(notes.id) > 0
                ORDER BY tags.name COLLATE NOCASE
                """
            ).fetchall()
            return [{"name": row["name"], "count": int(row["count"])} for row in rows]

    def get_stats(self) -> dict[str, int]:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN pinned = 1 AND archived = 0 AND trashed = 0 THEN 1 ELSE 0 END) AS pinned,
                    SUM(CASE WHEN archived = 1 AND trashed = 0 THEN 1 ELSE 0 END) AS archived,
                    SUM(CASE WHEN trashed = 1 THEN 1 ELSE 0 END) AS trashed
                FROM notes
                """
            ).fetchone()
            tags = connection.execute("SELECT COUNT(*) AS count FROM tags").fetchone()
            assert row is not None and tags is not None
            return {
                "total": int(row["total"] or 0),
                "pinned": int(row["pinned"] or 0),
                "archived": int(row["archived"] or 0),
                "trashed": int(row["trashed"] or 0),
                "tags": int(tags["count"] or 0),
            }

    def delete_note(self, note_id: str) -> list[dict[str, Any]]:
        attachments = self.list_attachments(note_id)
        with self.connection() as connection:
            cursor = connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            if cursor.rowcount == 0:
                raise NoteNotFoundError(note_id)
            connection.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM note_tags)")
        return attachments

    def create_attachment(
        self,
        *,
        attachment_id: str,
        note_id: str,
        filename: str,
        stored_name: str,
        content_type: str,
        size: int,
    ) -> dict[str, Any]:
        now = int(time.time())
        with self.connection() as connection:
            note = connection.execute("SELECT id FROM notes WHERE id = ?", (note_id,)).fetchone()
            if note is None:
                raise NoteNotFoundError(note_id)
            connection.execute(
                """
                INSERT INTO attachments(
                    id, note_id, filename, stored_name, content_type, size, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (attachment_id, note_id, filename, stored_name, content_type, size, now),
            )
            return {
                "id": attachment_id,
                "note_id": note_id,
                "filename": filename,
                "stored_name": stored_name,
                "content_type": content_type,
                "size": size,
                "created_at": now,
            }

    def get_attachment(self, attachment_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
            if row is None:
                raise NoteNotFoundError(attachment_id)
            return dict(row)

    def list_attachments(self, note_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM attachments WHERE note_id = ? ORDER BY created_at DESC",
                (note_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_attachment(self, attachment_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
            if row is None:
                raise NoteNotFoundError(attachment_id)
            connection.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
            return dict(row)

    def export_data(self) -> dict[str, Any]:
        with self.connection() as connection:
            note_rows = connection.execute(
                "SELECT * FROM notes ORDER BY created_at"
            ).fetchall()
            notes = [self._row_to_note(connection, row) for row in note_rows]
            attachment_rows = connection.execute(
                "SELECT * FROM attachments ORDER BY created_at"
            ).fetchall()
            return {
                "format": "qishi-note-vault",
                "version": 1,
                "exported_at": int(time.time()),
                "notes": notes,
                "attachments": [dict(row) for row in attachment_rows],
            }

    def import_data(self, payload: dict[str, Any]) -> dict[str, int]:
        if payload.get("format") != "qishi-note-vault" or not isinstance(payload.get("notes"), list):
            raise ValueError("invalid note vault backup format")
        imported = 0
        updated = 0
        now = int(time.time())
        with self.connection() as connection:
            for item in payload["notes"]:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                note_id = str(item["id"])
                existing = connection.execute("SELECT id FROM notes WHERE id = ?", (note_id,)).fetchone()
                title = str(item.get("title", "")).strip()[:200] or "Untitled"
                body = str(item.get("body", ""))
                created_at = int(item.get("created_at") or now)
                updated_at = int(item.get("updated_at") or now)
                connection.execute(
                    """
                    INSERT INTO notes(
                        id, title, body, created_at, updated_at, pinned, archived, trashed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        body = excluded.body,
                        updated_at = excluded.updated_at,
                        pinned = excluded.pinned,
                        archived = excluded.archived,
                        trashed = excluded.trashed
                    """,
                    (
                        note_id,
                        title,
                        body,
                        created_at,
                        updated_at,
                        int(bool(item.get("pinned"))),
                        int(bool(item.get("archived"))),
                        int(bool(item.get("trashed"))),
                    ),
                )
                self._set_tags(connection, note_id, item.get("tags", []))
                if existing is None:
                    imported += 1
                else:
                    updated += 1

            for item in payload.get("attachments", []):
                if not isinstance(item, dict) or not item.get("id") or not item.get("note_id"):
                    continue
                connection.execute(
                    """
                    INSERT OR REPLACE INTO attachments(
                        id, note_id, filename, stored_name, content_type, size, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item["id"]),
                        str(item["note_id"]),
                        str(item.get("filename", "attachment")),
                        str(item.get("stored_name", item["id"])),
                        str(item.get("content_type", "application/octet-stream")),
                        int(item.get("size", 0)),
                        int(item.get("created_at") or now),
                    ),
                )
        return {"imported": imported, "updated": updated}

    def set_note_flags(
        self,
        note_id: str,
        *,
        pinned: bool | None = None,
        archived: bool | None = None,
        trashed: bool | None = None,
    ) -> dict[str, Any]:
        return self.update_note(
            note_id,
            pinned=pinned,
            archived=archived,
            trashed=trashed,
        )
