from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TraceEvent:
    type: str
    data: dict[str, Any]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class TraceLogger:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.events: list[TraceEvent] = []

    def record(self, event_type: str, **data: Any) -> None:
        self.events.append(TraceEvent(type=event_type, data=data))

    def flush(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(event) for event in self.events]
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
