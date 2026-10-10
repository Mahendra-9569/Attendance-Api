import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import HTTPException

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
PRESENCE_STATUSES = ("PRESENT", "WFH", "ON_DUTY")
ALL_STATUSES = ("PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY")
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000


def parse_date(value: str, field_name: str = "date") -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{field_name} must be YYYY-MM-DD")
    if parsed.isoformat() != value:
        raise HTTPException(422, f"{field_name} must be YYYY-MM-DD")
    return parsed


def parse_month(month: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "month must be YYYY-MM")
    year, mon = map(int, month.split("-"))
    first = date(year, mon, 1)
    if mon == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, mon + 1, 1)
    return first, next_month - timedelta(days=1)


def validate_epoch_ms(value: Optional[int]) -> None:
    if value is not None and not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
        raise HTTPException(422, "timestamp must be epoch milliseconds")


def now_utc_seconds() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def epoch_ms_to_datetime(value: int) -> datetime:
    validate_epoch_ms(value)
    return datetime.fromtimestamp(value / 1000, UTC).replace(microsecond=0)


def datetime_to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def truncate_seconds(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def ist_datetime(day: date, clock: str, next_day: bool = False) -> datetime:
    h, m = map(int, clock.split(":"))
    actual_day = day + timedelta(days=1) if next_day else day
    return datetime.combine(actual_day, time(h, m), tzinfo=IST)


def is_overnight(shift_start: str, shift_end: str) -> bool:
    return shift_end <= shift_start


def attendance_date(punched_at: datetime, employee: dict) -> date:
    local = punched_at.astimezone(IST)
    shift_end = employee["shift_end"]
    if is_overnight(employee["shift_start"], shift_end):
        end_h, end_m = map(int, shift_end.split(":"))
        if (local.hour, local.minute) < (end_h, end_m):
            return local.date() - timedelta(days=1)
    return local.date()


def shift_start_datetime(record_date: str, employee: dict) -> datetime:
    day = parse_date(record_date)
    return ist_datetime(day, employee["shift_start"])


def shift_end_datetime(record_date: str, employee: dict) -> datetime:
    day = parse_date(record_date)
    return ist_datetime(
        day,
        employee["shift_end"],
        next_day=is_overnight(employee["shift_start"], employee["shift_end"]),
    )


def compute_late_minutes(punch_in: Optional[datetime], employee: dict, record_date: str) -> int:
    if punch_in is None:
        return 0
    start = shift_start_datetime(record_date, employee)
    elapsed = int((punch_in.astimezone(UTC) - start.astimezone(UTC)).total_seconds())
    if elapsed > 10 * 60:
        return max(0, elapsed // 60)
    return 0


def half_up(value: float, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def compute_derived(record: dict, employee: dict) -> dict:
    """Return the API-owned derived fields for the record's final punch times."""
    punch_in = record.get("punch_in")
    punch_out = record.get("punch_out")
    status = record.get("status")

    if status not in PRESENCE_STATUSES:
        return {
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }

    late_minutes = compute_late_minutes(punch_in, employee, record["date"])
    if punch_in is None or punch_out is None:
        return {
            "work_hours": None,
            "late_minutes": late_minutes,
            "overtime_minutes": 0,
            "half_day": False,
        }

    seconds = int((punch_out - punch_in).total_seconds())
    work_hours = half_up(seconds / 3600, 2)
    shift_end = shift_end_datetime(record["date"], employee).astimezone(UTC)
    overtime_seconds = int((punch_out - shift_end).total_seconds())
    overtime_minutes = overtime_seconds // 60 if overtime_seconds >= 30 * 60 else 0
    return {
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": max(0, overtime_minutes),
        "half_day": work_hours < 4.50,
    }


def round_pipeline(expression, places: int = 2):
    """MongoDB half-up rounding for non-negative numbers (unlike $round ties-to-even)."""
    scale = 10 ** places
    return {
        "$cond": [
            {"$eq": [expression, None]},
            None,
            {"$divide": [{"$floor": {"$add": [{"$multiply": [expression, scale]}, 0.5]}}, scale]},
        ]
    }


def public_employee(doc: dict) -> dict:
    result = {key: value for key, value in doc.items() if key != "_id"}
    if "created_at" in result:
        result["created_at"] = datetime_to_epoch_ms(result["created_at"])
    return result


def public_attendance(doc: dict) -> dict:
    result = {key: value for key, value in doc.items() if key != "_id"}
    result["punch_in"] = datetime_to_epoch_ms(result.get("punch_in"))
    result["punch_out"] = datetime_to_epoch_ms(result.get("punch_out"))
    result["history"] = result.get("history") or []
    result["half_day"] = result.get("half_day", False)
    result["late_minutes"] = result.get("late_minutes", 0)
    result["overtime_minutes"] = result.get("overtime_minutes", 0)
    for entry in result["history"]:
        entry["at"] = datetime_to_epoch_ms(entry.get("at"))
        changes = entry.get("changes", {})
        for field in ("punch_in", "punch_out"):
            if field in changes:
                changes[field]["from"] = datetime_to_epoch_ms(changes[field].get("from"))
                changes[field]["to"] = datetime_to_epoch_ms(changes[field].get("to"))
    return result


def workdays_between(start: date, end: date) -> int:
    """Count Monday-Friday dates inclusively, without a holiday calendar."""
    if end < start:
        return 0
    weeks, extra = divmod((end - start).days + 1, 7)
    count = weeks * 5
    for offset in range(extra):
        if (start + timedelta(days=weeks * 7 + offset)).weekday() < 5:
            count += 1
    return count


def month_date_filter(month: str) -> tuple[str, str, date, date]:
    first, last = parse_month(month)
    return first.isoformat(), last.isoformat(), first, last


def validate_page(page: int, page_size: int) -> None:
    if page < 1:
        raise HTTPException(422, "page must be at least 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")


def attendance_query(
    emp_code: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    status: Optional[str],
) -> dict:
    query = {}
    if emp_code:
        query["emp_code"] = emp_code
    if date_from:
        parse_date(date_from, "date_from")
    if date_to:
        parse_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must not be after date_to")
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    if status:
        if status not in ALL_STATUSES:
            raise HTTPException(422, "invalid status")
        query["status"] = status
    return query
