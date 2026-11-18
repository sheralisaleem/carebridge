from datetime import datetime
from typing import Any

from ..clinic_db import connect
class ClinicEnvironment:
    """Shared world: clinic clock, database path, and the latest known occupancy."""

    def __init__(self, db_path, now: datetime):
        self.db_path = db_path
        self.now = now
        self.tick = 0
        self.faults: dict[str, Any] = {}
        self.metrics = {
            "bookings": 0, "refusals": 0, "conflicts": 0,
            "cancellations": 0, "messages": 0, "replans": 0,
        }

    def occupancy(self) -> dict:
        with connect(self.db_path) as conn:
            open_n = conn.execute("SELECT COUNT(*) c FROM slots WHERE status='open'").fetchone()["c"]
            booked = conn.execute("SELECT COUNT(*) c FROM slots WHERE status='booked'").fetchone()["c"]
            blocked = conn.execute("SELECT COUNT(*) c FROM slots WHERE status='blocked'").fetchone()["c"]
            appts = conn.execute(
                "SELECT COUNT(*) c FROM appointments WHERE status='confirmed'"
            ).fetchone()["c"]
        return {"open": open_n, "booked": booked, "blocked": blocked, "confirmed_appointments": appts}

    def snapshot(self) -> dict:
        return {"tick": self.tick, "now": self.now.isoformat(), "occupancy": self.occupancy(),
                "metrics": dict(self.metrics), "faults": dict(self.faults)}
