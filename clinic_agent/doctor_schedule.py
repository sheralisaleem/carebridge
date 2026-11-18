"""Doctor-owned weekly hours, one-off slots and the shared appointment agenda."""
from datetime import date, datetime, time, timedelta

from .clinic_db import DOCTORS, connect, slot_id_for

HORIZON = 21
DEPARTMENTS = tuple(dict.fromkeys(doctor[2] for doctor in DOCTORS))
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
BOOKING_WINDOWS = (((540, 720), (840, 1020)),) * 5 + (((540, 780),), ())
SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_hours (
    doctor_id TEXT NOT NULL REFERENCES doctors(doctor_id),
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    PRIMARY KEY (doctor_id, weekday, start_time)
);
CREATE TABLE IF NOT EXISTS slot_overrides (
    doctor_id TEXT NOT NULL REFERENCES doctors(doctor_id),
    slot_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    PRIMARY KEY (doctor_id, slot_date, start_time)
);
CREATE TABLE IF NOT EXISTS doctor_leave (
    doctor_id TEXT NOT NULL REFERENCES doctors(doctor_id),
    slot_date TEXT NOT NULL,
    PRIMARY KEY (doctor_id, slot_date)
);
CREATE TABLE IF NOT EXISTS calendar_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ScheduleError(ValueError):
    pass


def clock_time(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def minutes(value: str) -> int:
    if not isinstance(value, str):
        raise ScheduleError("Enter a time in HH:MM format.")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ScheduleError("Enter a time in HH:MM format.") from exc
    if value != f"{parsed.hour:02d}:{parsed.minute:02d}" or parsed.minute % 30:
        raise ScheduleError("Choose a time on a 30-minute boundary.")
    return parsed.hour * 60 + parsed.minute


def initialize(conn) -> None:
    conn.executescript(SCHEMA)
    conn.execute("BEGIN IMMEDIATE")
    if conn.execute("SELECT 1 FROM calendar_metadata WHERE key = 'initialized'").fetchone():
        return
    # Import the original fixture hours once. New profiles start with no hours.
    for doctor_id, *_, weekdays in DOCTORS:
        if not conn.execute("SELECT 1 FROM doctors WHERE doctor_id = ?", (doctor_id,)).fetchone():
            continue
        conn.executemany(
            "INSERT INTO weekly_hours VALUES (?, ?, ?, ?)",
            [(doctor_id, day, clock_time(start), clock_time(end))
             for day in weekdays for start, end in BOOKING_WINDOWS[day]],
        )
    conn.execute(
        "INSERT OR IGNORE INTO slot_overrides "
        "SELECT doctor_id, slot_date, start_time, 0 FROM slots WHERE status = 'blocked'"
    )
    conn.execute("INSERT INTO calendar_metadata VALUES ('initialized', '1')")


def weekly_hours(conn, doctor_id: str) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT weekday, start_time, end_time FROM weekly_hours WHERE doctor_id = ? "
        "ORDER BY weekday, start_time", (doctor_id,)
    )]


def release_slot(conn, slot_id: str) -> None:
    slot = conn.execute("SELECT * FROM slots WHERE slot_id = ?", (slot_id,)).fetchone()
    key = (slot["doctor_id"], slot["slot_date"])
    leave = conn.execute("SELECT 1 FROM doctor_leave WHERE doctor_id = ? AND slot_date = ?", key).fetchone()
    override = conn.execute(
        "SELECT available FROM slot_overrides WHERE doctor_id = ? AND slot_date = ? AND start_time = ?",
        (*key, slot["start_time"]),
    ).fetchone()
    recurring = conn.execute(
        "SELECT 1 FROM weekly_hours WHERE doctor_id = ? AND weekday = ? AND start_time <= ? AND end_time > ?",
        (slot["doctor_id"], date.fromisoformat(slot["slot_date"]).weekday(), slot["start_time"], slot["start_time"]),
    ).fetchone()
    available = not leave and (bool(override["available"]) if override else bool(recurring))
    conn.execute("UPDATE slots SET status = ? WHERE slot_id = ?", ("open" if available else "blocked", slot_id))


class DoctorSchedule:
    def __init__(self, db_path, now: datetime):
        self.db_path = db_path
        self.now = now
        self.first_day = now.date()
        self.last_day = self.first_day + timedelta(days=HORIZON)

    def prepare(self) -> None:
        with connect(self.db_path) as conn:
            initialize(conn)
            for row in conn.execute("SELECT doctor_id FROM doctors").fetchall():
                self._sync(conn, row["doctor_id"])

    def _sync(self, conn, doctor_id, day_only=None):
        hours = {day: [] for day in range(7)}
        for row in weekly_hours(conn, doctor_id):
            hours[row["weekday"]].extend(
                clock_time(start) for start in range(minutes(row["start_time"]), minutes(row["end_time"]), 30)
            )
        days = [day_only] if day_only else [
            self.first_day + timedelta(days=offset) for offset in range(HORIZON + 1)
        ]
        lower, upper = days[0].isoformat(), days[-1].isoformat()
        overrides = {}
        for row in conn.execute(
            "SELECT slot_date, start_time, available FROM slot_overrides "
            "WHERE doctor_id = ? AND slot_date BETWEEN ? AND ?", (doctor_id, lower, upper)
        ):
            overrides.setdefault(row["slot_date"], {})[row["start_time"]] = bool(row["available"])
        leave = {row["slot_date"] for row in conn.execute(
            "SELECT slot_date FROM doctor_leave WHERE doctor_id = ? AND slot_date BETWEEN ? AND ?",
            (doctor_id, lower, upper),
        )}
        for day in days:
            iso = day.isoformat()
            planned = dict.fromkeys(hours[day.weekday()], True)
            planned.update(overrides.get(iso, {}))
            if iso in leave:
                planned = dict.fromkeys(planned, False)
            old = {row["start_time"]: row["status"] for row in conn.execute(
                "SELECT start_time, status FROM slots WHERE doctor_id = ? AND slot_date = ?", (doctor_id, iso)
            )}
            for start in old.keys() | planned.keys():
                if old.get(start) == "booked" or datetime.fromisoformat(f"{iso}T{start}") < self.now:
                    continue
                status = "open" if planned.get(start) else "blocked"
                if status != old.get(start):
                    conn.execute(
                        "INSERT INTO slots VALUES (?, ?, ?, ?, 30, ?) "
                        "ON CONFLICT(slot_id) DO UPDATE SET status = excluded.status",
                        (slot_id_for(doctor_id, day, start), doctor_id, iso, start, status),
                    )

    @staticmethod
    def _coerce_windows(windows):
        if not isinstance(windows, list) or len(windows) > 28:
            raise ScheduleError("Enter up to 28 weekly time ranges.")
        result = []
        for row in windows:
            if not isinstance(row, dict) or type(row.get("weekday")) is not int or not 0 <= row["weekday"] < 7:
                raise ScheduleError("Choose a day of the week for every time range.")
            day = row["weekday"]
            start, end = minutes(row.get("start_time")), minutes(row.get("end_time"))
            if start >= end or not any(lo <= start < end <= hi for lo, hi in BOOKING_WINDOWS[day]):
                raise ScheduleError("Use time ranges within the clinic's appointment hours.")
            result.append((day, start, end))
        result.sort()
        if any(day == prev_day and start < prev_end
               for (prev_day, _, prev_end), (day, start, _) in zip(result, result[1:])):
            raise ScheduleError("Weekly time ranges cannot overlap.")
        return result

    def set_weekly_hours(self, doctor_id, windows):
        validated = self._coerce_windows(windows)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM weekly_hours WHERE doctor_id = ?", (doctor_id,))
            conn.executemany(
                "INSERT INTO weekly_hours VALUES (?, ?, ?, ?)",
                [(doctor_id, day, clock_time(start), clock_time(end)) for day, start, end in validated],
            )
            self._sync(conn, doctor_id)
        return {"kept": self._outside_hours_count(doctor_id)}

    def _day(self, day):
        try:
            value = date.fromisoformat(day)
        except (ValueError, TypeError) as exc:
            raise ScheduleError("Choose a valid date.") from exc
        if not self.first_day <= value <= self.last_day:
            raise ScheduleError("Choose a date in the current 21-day booking window.")
        return value

    def set_slot(self, doctor_id, day, start_time, available):
        day = self._day(day)
        start = minutes(start_time)
        if type(available) is not bool or not any(lo <= start and start + 30 <= hi
                                                for lo, hi in BOOKING_WINDOWS[day.weekday()]):
            raise ScheduleError("Choose a 30-minute time within the clinic's appointment hours.")
        if datetime.combine(day, time.fromisoformat(start_time)) < self.now:
            raise ScheduleError("Choose a time in the future.")
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            key = (doctor_id, day.isoformat())
            if conn.execute("SELECT 1 FROM doctor_leave WHERE doctor_id = ? AND slot_date = ?", key).fetchone():
                raise ScheduleError("This date is covered by clinic leave.")
            slot = conn.execute("SELECT status FROM slots WHERE slot_id = ?",
                                (slot_id_for(doctor_id, day, start_time),)).fetchone()
            if slot and slot["status"] == "booked":
                raise ScheduleError("This time is booked. The confirmed visit stays on your calendar.")
            conn.execute(
                "INSERT INTO slot_overrides VALUES (?, ?, ?, ?) "
                "ON CONFLICT(doctor_id, slot_date, start_time) DO UPDATE SET available = excluded.available",
                (*key, start_time, available),
            )
            self._sync(conn, doctor_id, day)

    def availability(self, doctor_id, day):
        date_value = self._day(day)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT start_time, status FROM slots WHERE doctor_id = ? AND slot_date = ? ORDER BY start_time",
                (doctor_id, date_value.isoformat()),
            )
            return {"hours": weekly_hours(conn, doctor_id), "slots": [dict(row) for row in rows]}

    def appointments(self, doctor_id):
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT a.appointment_id, p.name AS patient, s.slot_date AS date, s.start_time,
                          a.status, so.available AS override,
                          EXISTS (SELECT 1 FROM weekly_hours w
                                  WHERE w.doctor_id = s.doctor_id
                                    AND w.weekday = (CAST(strftime('%w', s.slot_date) AS INTEGER) + 6) % 7
                                    AND w.start_time <= s.start_time AND w.end_time > s.start_time) AS weekly
                   FROM appointments a JOIN patients p ON p.patient_id = a.patient_id
                   JOIN slots s ON a.slot_id = s.slot_id
                   LEFT JOIN slot_overrides so ON so.doctor_id = s.doctor_id AND so.slot_date = s.slot_date
                                              AND so.start_time = s.start_time
                   WHERE s.doctor_id = ? AND a.status = 'confirmed'
                     AND (s.slot_date > ? OR (s.slot_date = ? AND s.start_time >= ?))
                   ORDER BY s.slot_date, s.start_time""",
                (doctor_id, self.first_day.isoformat(), self.first_day.isoformat(), self.now.strftime("%H:%M")),
            )
            result = []
            for row in rows:
                item = dict(row)
                override, weekly = item.pop("override"), item.pop("weekly")
                item["outside_hours"] = not (weekly if override is None else override)
                result.append(item)
            return result

    def _outside_hours_count(self, doctor_id):
        return sum(item["outside_hours"] for item in self.appointments(doctor_id))
