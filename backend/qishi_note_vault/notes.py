"""Application service for notes, attachments, and backups."""

from __future__ import annotations

import io
import json
import mimetypes
import os
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .database import Database, NoteNotFoundError

MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_BACKUP_BYTES = 64 * 1024 * 1024
MAX_BACKUP_JSON_BYTES = 10 * 1024 * 1024
MAX_BACKUP_MEMBERS = 10_000
ALLOWED_ATTACHMENT_SUFFIXES = {
    ".csv",
    ".gif",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".pdf",
    ".png",
    ".svg",
    ".txt",
    ".webp",
    ".zip",
}


class AttachmentError(ValueError):
    """Raised when an attachment fails validation."""


class NoteService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.attachment_dir = self.data_dir / "attachments"
        self.attachment_dir.mkdir(parents=True, exist_ok=True)
        self.database = Database(self.data_dir / "notes.db")

    def create_note(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.database.create_note(
            title=payload.get("title", ""),
            body=payload.get("body", ""),
            tags=payload.get("tags", []),
        )

    def update_note(self, note_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"title", "body", "tags", "pinned", "archived", "trashed"}
        values = {key: payload[key] for key in allowed if key in payload}
        return self.database.update_note(note_id, **values)

    def get_note(self, note_id: str) -> dict[str, Any]:
        note = self.database.get_note(note_id)
        note["attachments"] = self.database.list_attachments(note_id)
        return note

    def list_notes(
        self,
        *,
        query: str = "",
        tag: str = "",
        view: str = "active",
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        return {
            "items": self.database.list_notes(
                query=query,
                tag=tag,
                view=view,
                limit=limit,
                offset=offset,
            ),
            "stats": self.database.get_stats(),
        }

    def save_attachment(
        self,
        *,
        note_id: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        if not data:
            raise AttachmentError("attachment is empty")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise AttachmentError("attachment exceeds 8 MB")

        self.database.get_note(note_id)
        safe_filename = Path(filename or "attachment").name[:180]
        suffix = Path(safe_filename).suffix.lower()
        if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
            raise AttachmentError("attachment file type is not allowed")

        attachment_id = str(uuid.uuid4())
        stored_name = attachment_id + suffix
        target = self.attachment_dir / stored_name
        target.write_bytes(data)
        guessed_type = content_type or mimetypes.guess_type(safe_filename)[0]
        record = self.database.create_attachment(
            attachment_id=attachment_id,
            note_id=note_id,
            filename=safe_filename,
            stored_name=stored_name,
            content_type=guessed_type or "application/octet-stream",
            size=len(data),
        )
        return record

    def read_attachment(self, attachment_id: str) -> tuple[dict[str, Any], bytes]:
        record = self.database.get_attachment(attachment_id)
        target = (self.attachment_dir / record["stored_name"]).resolve()
        if target.parent != self.attachment_dir.resolve() or not target.is_file():
            raise NoteNotFoundError(attachment_id)
        return record, target.read_bytes()

    def delete_attachment(self, attachment_id: str) -> dict[str, Any]:
        record = self.database.delete_attachment(attachment_id)
        target = self.attachment_dir / record["stored_name"]
        if target.is_file():
            target.unlink()
        return record

    def delete_note(self, note_id: str, *, permanent: bool = False) -> dict[str, Any]:
        if not permanent:
            return self.database.update_note(note_id, trashed=True) | {"permanent": False}
        attachments = self.database.delete_note(note_id)
        for item in attachments:
            target = self.attachment_dir / item["stored_name"]
            if target.is_file():
                target.unlink()
        return {"id": note_id, "permanent": True, "attachments_removed": len(attachments)}

    def export_backup(self) -> bytes:
        payload = self.database.export_data()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "notes.json",
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for item in payload["attachments"]:
                source = self.attachment_dir / item["stored_name"]
                if source.is_file():
                    archive.write(source, "attachments/" + item["stored_name"])
        return buffer.getvalue()

    def import_backup(self, data: bytes) -> dict[str, int]:
        if len(data) > MAX_BACKUP_BYTES:
            raise AttachmentError("backup exceeds 64 MB")
        try:
            archive = zipfile.ZipFile(io.BytesIO(data), "r")
        except zipfile.BadZipFile as exc:
            raise AttachmentError("backup must be a valid zip archive") from exc

        with archive:
            members = archive.infolist()
            if len(members) > MAX_BACKUP_MEMBERS:
                raise AttachmentError("backup contains too many files")
            try:
                payload_bytes = archive.read("notes.json")
                if len(payload_bytes) > MAX_BACKUP_JSON_BYTES:
                    raise AttachmentError("backup notes.json exceeds 10 MB")
                payload = json.loads(payload_bytes.decode("utf-8"))
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AttachmentError("backup is missing valid notes.json") from exc

            result = self.database.import_data(payload)
            known_names = {str(item.get("stored_name", "")) for item in payload.get("attachments", [])}
            total_size = 0
            for member in members:
                if not member.filename.startswith("attachments/"):
                    continue
                stored_name = Path(member.filename).name
                if stored_name not in known_names:
                    continue
                total_size += member.file_size
                if total_size > MAX_BACKUP_BYTES:
                    raise AttachmentError("backup attachments exceed 64 MB")
                target = self.attachment_dir / stored_name
                target.write_bytes(archive.read(member))
        return result


def default_data_dir() -> Path:
    return Path(
        os.environ.get(
            "NOTE_VAULT_DATA_DIR",
            os.environ.get("CONFIG_DATA", "/usr/local/qishi-note-vault/data"),
        )
    ).resolve()
