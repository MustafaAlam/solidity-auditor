"""Append-only findings ledger — the shared blackboard.

Every hunt agent owns exactly one file and never touches another's. That is what
makes a 25-way concurrent fan-out safe with no locking, and it is what makes a
crashed run recoverable: whatever reached disk is intact and valid.

The ledger is also the boundary where untrusted agent output becomes trusted
pipeline input. Nothing gets past ``append`` without passing the schema.
"""

from __future__ import annotations

import json
import pathlib
import threading
from collections.abc import Iterator

from pydantic import ValidationError

from ..schemas import Finding


class Ledger:
    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- paths ----------------------------------------------------------
    def path_for(self, agent_id: str) -> pathlib.Path:
        return self.root / f"agent-{agent_id}.jsonl"

    @property
    def quarantine_path(self) -> pathlib.Path:
        return self.root / "quarantine.jsonl"

    def _lock(self, name: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(name, threading.Lock())

    # -- writing --------------------------------------------------------
    def append(self, agent_id: str, record: dict) -> tuple[bool, list[str]]:
        """Validate then append. Returns (accepted, errors).

        A rejected record is written to quarantine, never dropped. "Nobody
        reported anything" and "the report was unparseable" must not look alike
        downstream.
        """
        try:
            finding = Finding.model_validate(record)
        except ValidationError as exc:
            errors = [f"{'/'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            self._write(self.quarantine_path, {**record, "_agent_id": agent_id, "_errors": errors})
            return False, errors

        self._write(self.path_for(agent_id), finding.model_dump(mode="json", exclude_none=True))
        return True, []

    def append_many(self, agent_id: str, records: list[dict]) -> tuple[int, int, list[str]]:
        accepted = rejected = 0
        all_errors: list[str] = []
        for rec in records:
            ok, errs = self.append(agent_id, rec)
            if ok:
                accepted += 1
            else:
                rejected += 1
                all_errors.extend(errs)
        return accepted, rejected, all_errors

    def _write(self, path: pathlib.Path, payload: dict) -> None:
        with self._lock(path.name):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
                fh.flush()  # a crash after this line still leaves the record

    # -- reading --------------------------------------------------------
    def read_all(self) -> list[Finding]:
        out: list[Finding] = []
        for path in sorted(self.root.glob("agent-*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(Finding.model_validate_json(line))
        return out

    def iter_raw(self) -> Iterator[dict]:
        for path in sorted(self.root.glob("agent-*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    yield json.loads(line)

    def quarantined(self) -> list[dict]:
        if not self.quarantine_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.quarantine_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def agents_with_output(self) -> set[str]:
        return {p.stem.removeprefix("agent-") for p in self.root.glob("agent-*.jsonl") if p.stat().st_size > 0}
