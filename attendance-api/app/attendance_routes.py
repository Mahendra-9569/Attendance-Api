from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from app.database import db
from app.helpers import *
from app.models import EmployeeCreate, PunchInRequest, PunchOutRequest, RegularizeRequest

router = APIRouter()


@router.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "employee not found")

    punched_at = epoch_ms_to_datetime(body.punched_at) if body.punched_at is not None else now_utc_seconds()
    record_day = attendance_date(punched_at, employee).isoformat()
    record = {
        "emp_code": body.emp_code,
        "date": record_day,
        "status": body.status,
        "punch_in": punched_at,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": 0,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    record.update(compute_derived(record, employee))
    try:
        db.attendance_logs.insert_one(record)
    except DuplicateKeyError:
        raise HTTPException(409, "attendance already exists for this employee and date")
    return public_attendance(record)


@router.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "employee not found")

    punched_at = epoch_ms_to_datetime(body.punched_at) if body.punched_at is not None else now_utc_seconds()
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punched_at}},
        sort=[("punch_in", DESCENDING)],
    )
    if record is None:
        raise HTTPException(404, "no punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(409, "this attendance record is already punched out")
    seconds = (punched_at - record["punch_in"]).total_seconds()
    if seconds <= 0 or seconds > 24 * 60 * 60:
        raise HTTPException(422, "punch-out must be after punch-in and within 24 hours")

    updated = dict(record)
    updated["punch_out"] = punched_at
    updated.update(compute_derived(updated, employee))
    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": punched_at,
            "work_hours": updated["work_hours"],
            "late_minutes": updated["late_minutes"],
            "overtime_minutes": updated["overtime_minutes"],
            "half_day": updated["half_day"],
        }},
    )
    if result.modified_count != 1:
        raise HTTPException(409, "this attendance record was updated by another request")
    return public_attendance(updated)


@router.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = attendance_query(emp_code, date_from, date_to, status)
    total = db.attendance_logs.count_documents(query)
    docs = db.attendance_logs.find(query).sort(
        [("date", DESCENDING), ("emp_code", ASCENDING)]
    ).skip((page - 1) * page_size).limit(page_size)
    return {
        "items": [public_attendance(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    parse_date(date)
    if "status" in body.model_fields_set and body.status is None:
        raise HTTPException(422, "status must be a valid status value")
    if ("punch_in" in body.model_fields_set and body.punch_in is None) or (
        "punch_out" in body.model_fields_set and body.punch_out is None
    ):
        raise HTTPException(422, "punch timestamps must be omitted or valid epoch milliseconds")
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "employee not found")

    original = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if original is None:
        raise HTTPException(404, "attendance record not found")

    status = body.status if "status" in body.model_fields_set else original["status"]
    if status in ("ABSENT", "LEAVE"):
        if "punch_in" in body.model_fields_set or "punch_out" in body.model_fields_set:
            raise HTTPException(422, "ABSENT and LEAVE records cannot have punch times")
        punch_in = None
        punch_out = None
    else:
        punch_in = (
            epoch_ms_to_datetime(body.punch_in)
            if "punch_in" in body.model_fields_set and body.punch_in is not None
            else original.get("punch_in")
        )
        punch_out = (
            epoch_ms_to_datetime(body.punch_out)
            if "punch_out" in body.model_fields_set and body.punch_out is not None
            else original.get("punch_out")
        )
        if punch_in is None:
            raise HTTPException(422, "presence status requires punch_in")

    if punch_in is not None:
        expected_day = attendance_date(punch_in, employee).isoformat()
        if expected_day != date:
            raise HTTPException(422, "punch_in must belong to the record's attendance date")
    if punch_out is not None:
        if punch_in is None:
            raise HTTPException(422, "punch_out requires punch_in")
        duration = (punch_out - punch_in).total_seconds()
        if duration <= 0 or duration > 24 * 60 * 60:
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

    updated = dict(original)
    updated["status"] = status
    updated["punch_in"] = punch_in
    updated["punch_out"] = punch_out
    updated.update(compute_derived(updated, employee))

    tracked_fields = (
        "status", "punch_in", "punch_out", "work_hours",
        "late_minutes", "overtime_minutes", "half_day",
    )
    changes = {}
    for field in tracked_fields:
        before = original.get(field)
        after = updated.get(field)
        if field in ("late_minutes", "overtime_minutes"):
            before = original.get(field, 0)
        elif field == "half_day":
            before = original.get(field, False)
        if before != after:
            changes[field] = {"from": before, "to": after}
    if not changes:
        raise HTTPException(422, "the request does not change this record")

    history = original.get("history", [])
    history_entry = {
        "at": now_utc_seconds(),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Compare the values that were read as well as the history. This makes a
    # concurrent correction fail with 409 instead of overwriting another edit.
    update_filter = {
        "_id": original["_id"],
        "status": original.get("status"),
        "punch_in": original.get("punch_in"),
        "punch_out": original.get("punch_out"),
        "history": history,
    }
    if "history" not in original:
        update_filter.pop("history")
        update_filter["history"] = {"$exists": False}

    try:
        result = db.attendance_logs.update_one(
            update_filter,
            {
                "$set": {
                    "status": updated["status"],
                    "punch_in": updated["punch_in"],
                    "punch_out": updated["punch_out"],
                    "work_hours": updated["work_hours"],
                    "late_minutes": updated["late_minutes"],
                    "overtime_minutes": updated["overtime_minutes"],
                    "half_day": updated["half_day"],
                },
                "$push": {"history": history_entry},
            },
        )
    except PyMongoError:
        raise HTTPException(409, "record changed while regularizing")
    if result.modified_count != 1:
        raise HTTPException(409, "record changed while regularizing")

    updated["history"] = history + [history_entry]
    return public_attendance(updated)
