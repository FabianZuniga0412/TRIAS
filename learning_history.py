"""Progreso agregado por usuario; nunca guarda frases ni audios."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


PRACTICE_CATALOG = {
    "Subject-verb agreement": ("She works every day.", "Con he, she e it, el verbo suele llevar -s en presente."),
    "Verb tense": ("I went to school yesterday.", "Usa pasado simple para acciones terminadas."),
    "Articles": ("I saw a dog in the park.", "Usa a o an para presentar algo no específico."),
    "Prepositions": ("I am interested in music.", "Algunas expresiones requieren una preposición fija."),
    "Word choice": ("I made a decision yesterday.", "Aprende combinaciones comunes de palabras."),
    "Natural phrasing": ("I want to have a party.", "Practica expresiones frecuentes en conversaciones cotidianas."),
    "Sentence structure": ("Please send me the details.", "El orden claro de la oración mejora la comprensión."),
}
GENERAL_PRACTICE = ("I practice English every day.", "Una práctica corta y constante ayuda a ganar fluidez.")


@dataclass
class LearningHistoryStore:
    path: Path
    users: dict[str, dict] = field(default_factory=dict)

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw_users = json.loads(self.path.read_text(encoding="utf-8")).get("users", {})
        except (OSError, json.JSONDecodeError):
            self.users = {}
            return
        if not isinstance(raw_users, dict):
            self.users = {}
            return
        self.users = {}
        for user_id, raw_entry in raw_users.items():
            if not isinstance(user_id, str) or not isinstance(raw_entry, dict):
                continue
            counts = raw_entry.get("focus_counts", {})
            if not isinstance(counts, dict):
                counts = {}
            safe_counts = {
                focus: count
                for focus, count in counts.items()
                if focus in PRACTICE_CATALOG and isinstance(count, int) and count > 0
            }
            last_focus = raw_entry.get("last_focus")
            self.users[user_id] = {
                "last_focus": last_focus if isinstance(last_focus, str) and last_focus in PRACTICE_CATALOG else None,
                "focus_counts": safe_counts,
            }

    def record(self, user_id: int, focus: str) -> None:
        if focus not in PRACTICE_CATALOG:
            raise ValueError(f"Tema de práctica no permitido: {focus}")
        entry = self.users.setdefault(str(user_id), {"last_focus": None, "focus_counts": {}})
        entry["last_focus"] = focus
        counts = entry["focus_counts"]
        counts[focus] = int(counts.get(focus, 0)) + 1
        self._save()

    def practice_for(self, user_id: int) -> tuple[str, str, str | None]:
        focus = self.users.get(str(user_id), {}).get("last_focus")
        phrase, note = PRACTICE_CATALOG.get(focus, GENERAL_PRACTICE)
        return phrase, note, focus

    def summary_for(self, user_id: int) -> tuple[str | None, list[tuple[str, int]]]:
        entry = self.users.get(str(user_id), {})
        counts = entry.get("focus_counts", {})
        return entry.get("last_focus"), sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"users": self.users}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)
