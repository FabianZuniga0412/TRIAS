"""Progreso agregado por usuario; nunca guarda frases ni audios."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


PRACTICE_CATALOG = {
    "Subject-verb agreement": (
        ("She works every day.", "Con he, she e it, el verbo suele llevar -s en presente."),
        ("My brother plays soccer on Saturdays.", "Con my brother usamos plays porque equivale a he."),
        ("The teacher explains the lesson clearly.", "The teacher es singular, por eso usamos explains."),
    ),
    "Verb tense": (
        ("I went to school yesterday.", "Usa pasado simple para acciones terminadas."),
        ("They are studying for the exam now.", "Usa presente continuo para una acción que ocurre ahora."),
        ("We will visit our grandparents next weekend.", "Usa will para una acción futura."),
    ),
    "Articles": (
        ("I saw a dog in the park.", "Usa a o an para presentar algo no específico."),
        ("She bought an umbrella yesterday.", "Usa an antes de un sonido vocálico."),
        ("The movie was very interesting.", "Usa the cuando hablas de algo específico."),
    ),
    "Prepositions": (
        ("I am interested in music.", "Algunas expresiones requieren una preposición fija."),
        ("We arrived at the station on time.", "Usa at para un punto específico como una estación."),
        ("My keys are on the table.", "Usa on cuando algo está sobre una superficie."),
    ),
    "Word choice": (
        ("I made a decision yesterday.", "Aprende combinaciones comunes de palabras."),
        ("Could you give me some advice?", "Advice no suele usarse en plural en este contexto."),
        ("I am looking forward to the weekend.", "Looking forward to es una expresión fija."),
    ),
    "Natural phrasing": (
        ("I want to have a party.", "Practica expresiones frecuentes en conversaciones cotidianas."),
        ("I am twenty years old.", "En inglés usamos years old para decir la edad."),
        ("Could you give me a hand?", "Give me a hand es una forma natural de pedir ayuda."),
    ),
    "Sentence structure": (
        ("Please send me the details.", "El orden claro de la oración mejora la comprensión."),
        ("I do not know where he lives.", "En preguntas indirectas usamos el orden normal de la oración."),
        ("Because it was raining, we stayed home.", "La idea principal debe quedar completa después de because."),
    ),
}
GENERAL_PRACTICE = (
    ("I practice English every day.", "Una práctica corta y constante ayuda a ganar fluidez."),
    ("Could you repeat that, please?", "Es una frase útil para pedir repetición de forma amable."),
    ("I would like to order a coffee.", "Esta frase se usa para pedir algo de manera cortés."),
)


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
                "practice_indexes": self._safe_practice_indexes(raw_entry.get("practice_indexes")),
            }

    @staticmethod
    def _safe_practice_indexes(raw_indexes: object) -> dict[str, int]:
        if not isinstance(raw_indexes, dict):
            return {}
        allowed_keys = set(PRACTICE_CATALOG) | {"general"}
        return {
            key: value
            for key, value in raw_indexes.items()
            if isinstance(key, str) and key in allowed_keys and isinstance(value, int) and value >= 0
        }

    def record(self, user_id: int, focus: str) -> None:
        if focus not in PRACTICE_CATALOG:
            raise ValueError(f"Tema de práctica no permitido: {focus}")
        entry = self.users.setdefault(str(user_id), {"last_focus": None, "focus_counts": {}, "practice_indexes": {}})
        entry["last_focus"] = focus
        counts = entry["focus_counts"]
        counts[focus] = int(counts.get(focus, 0)) + 1
        self._save()

    def practice_for(self, user_id: int) -> tuple[str, str, str | None]:
        entry = self.users.setdefault(str(user_id), {"last_focus": None, "focus_counts": {}, "practice_indexes": {}})
        focus = entry["last_focus"]
        rotation_key = focus or "general"
        examples = PRACTICE_CATALOG.get(focus, GENERAL_PRACTICE)
        indexes = entry["practice_indexes"]
        index = int(indexes.get(rotation_key, 0)) % len(examples)
        phrase, note = examples[index]
        indexes[rotation_key] = (index + 1) % len(examples)
        self._save()
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
