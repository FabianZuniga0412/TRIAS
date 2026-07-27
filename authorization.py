"""Persistencia y reglas de autorizacion para el bot de Telegram."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AuthorizationStore:
    """Combina la configuracion base con altas y revocaciones persistentes."""

    path: Path
    base_users: frozenset[int] = field(default_factory=frozenset)
    admins: frozenset[int] = field(default_factory=frozenset)
    added_users: set[int] = field(default_factory=set, init=False)
    revoked_users: set[int] = field(default_factory=set, init=False)

    def load(self) -> None:
        if not self.path.exists():
            self._save()
            return

        try:
            raw_data = json.loads(self.path.read_text(encoding="utf-8"))
            self.added_users = {int(value) for value in raw_data.get("added_user_ids", [])}
            self.revoked_users = {int(value) for value in raw_data.get("revoked_user_ids", [])}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"No se pudo leer el archivo de autorizaciones: {self.path}"
            ) from exc

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admins

    def is_authorized(self, user_id: int) -> bool:
        return user_id in self.effective_users()

    def effective_users(self) -> set[int]:
        # Los administradores siempre retienen acceso, incluso si el JSON fue editado.
        return ((set(self.base_users) | self.added_users) - self.revoked_users) | set(self.admins)

    def allow(self, user_id: int) -> bool:
        """Autoriza un ID. Devuelve si la lista efectiva cambio."""
        was_authorized = self.is_authorized(user_id)
        self.revoked_users.discard(user_id)
        self.added_users.add(user_id)
        self._save()
        return not was_authorized

    def revoke(self, user_id: int) -> bool:
        """Revoca un ID no administrativo. Devuelve si se revoco efectivamente."""
        if self.is_admin(user_id):
            raise ValueError("No se puede revocar a un administrador.")

        was_authorized = self.is_authorized(user_id)
        self.added_users.discard(user_id)
        self.revoked_users.add(user_id)
        self._save()
        return was_authorized

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "added_user_ids": sorted(self.added_users),
            "revoked_user_ids": sorted(self.revoked_users),
        }
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary_path.replace(self.path)
