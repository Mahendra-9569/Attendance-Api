from typing import Literal, Optional
from fastapi import APIRouter, HTTPException, Query
from pymongo import ASCENDING, DESCENDING
from bson import json_util
from app.database import db
from app.helpers import *

router = APIRouter()


def employee_monthly_pipeline(emp_code: str, month: str) -> list[dict]:
    first, last, _, _ = month_date_filter(month)
    weekday = {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2, 3, 4, 5, 6]]}
    present_status = {"$in": ["$status", list(PRESENCE_STATUSES)]}
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": first, "$lte": last}}},
        {"$set": {"_weekday": weekday, "_is_present": present_status}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": ["$_weekday", "$_is_present"]},
                {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]},
                0,
            ]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        }},
        {"$project": {
            "_id": 0,
            "present_days": 1,
            "leave_days": 1,
            "late_count": 1,
            "total_late_minutes": 1,
            "total_overtime_minutes": 1,
        }},
    ]


@router.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    first, last, month_start, month_end = month_date_filter(month)
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "employee not found")
    start = max(month_start, parse_date(employee["joined_on"], "joined_on"))
    working_days = workdays_between(start, month_end) if start <= month_end else 0
    result = list(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code, month)))
    totals = result[0] if result else {}
    present_days = half_up(totals.get("present_days", 0), 2)
    attendance_pct = half_up(present_days / working_days * 100, 2) if working_days else None
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0),
        "total_late_minutes": totals.get("total_late_minutes", 0),
        "total_overtime_minutes": totals.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


def department_summary_pipeline(month: str, department: Optional[str] = None) -> list[dict]:
    first, last, _, month_end = month_date_filter(month)
    employee_match = {"joined_on": {"$lte": month_end.isoformat()}}
    if department is not None:
        employee_match["department"] = department
    weekday = {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, [2, 3, 4, 5, 6]]}
    is_present = {"$in": ["$logs.status", list(PRESENCE_STATUSES)]}
    return [
        {"$match": employee_match},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$emp_code", "$$code"]},
                    {"$gte": ["$date", first]},
                    {"$lte": ["$date", last]},
                ]}}},
            ],
            "as": "logs",
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$set": {
            "_has_log": {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},
            "_weekday": weekday,
            "_is_present": is_present,
            "_work_hours": "$logs.work_hours",
        }},
        {"$group": {
            "_id": {"department": "$department", "emp_code": "$emp_code"},
            "headcount": {"$first": 1},
            "present_days": {"$sum": {"$cond": [
                {"$and": ["$_has_log", "$_weekday", "$_is_present"]},
                {"$cond": [{"$ifNull": ["$logs.half_day", False]}, 0.5, 1]},
                0,
            ]}},
            "work_hours_sum": {"$sum": {"$cond": [
                {"$and": [
                    "$_has_log", "$_is_present",
                    {"$ne": [{"$ifNull": ["$_work_hours", None]}, None]},
                ]},
                "$_work_hours", 0,
            ]}},
            "work_hours_count": {"$sum": {"$cond": [
                {"$and": [
                    "$_has_log", "$_is_present",
                    {"$ne": [{"$ifNull": ["$_work_hours", None]}, None]},
                ]},
                1, 0,
            ]}},
            "late_count": {"$sum": {"$cond": [
                {"$and": ["$_has_log", {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}]},
                1, 0,
            ]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [
                {"$and": ["$_has_log", {"$eq": ["$logs.status", "LEAVE"]}]}, 1, 0,
            ]}},
            "on_duty_count": {"$sum": {"$cond": [
                {"$and": ["$_has_log", {"$eq": ["$logs.status", "ON_DUTY"]}]}, 1, 0,
            ]}},
        }},
        {"$group": {
            "_id": "$_id.department",
            "headcount": {"$sum": "$headcount"},
            "present_days": {"$sum": "$present_days"},
            "work_hours_sum": {"$sum": "$work_hours_sum"},
            "work_hours_count": {"$sum": "$work_hours_count"},
            "late_count": {"$sum": "$late_count"},
            "total_late_minutes": {"$sum": "$total_late_minutes"},
            "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"},
        }},
        {"$project": {
            "_id": 0,
            "department": "$_id",
            "headcount": 1,
            "present_days": 1,
            "avg_work_hours": {"$cond": [
                {"$gt": ["$work_hours_count", 0]},
                {"$divide": ["$work_hours_sum", "$work_hours_count"]},
                None,
            ]},
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1,
        }},
        {"$sort": {"department": ASCENDING}},
    ]


@router.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    parse_month(month)
    rows = list(db.employees.aggregate(department_summary_pipeline(month, department)))
    items = []
    for row in rows:
        if row["headcount"] == 0:
            continue
        row["present_days"] = half_up(row["present_days"], 2)
        row["avg_work_hours"] = (
            half_up(row["avg_work_hours"], 2) if row["avg_work_hours"] is not None else None
        )
        items.append(row)
    return {"month": month, "items": items}


def late_leaderboard_pipeline(month: str, limit: int, department: Optional[str] = None) -> list[dict]:
    first, last, _, _ = month_date_filter(month)
    pipeline = [
        {"$match": {
            "date": {"$gte": first, "$lte": last},
            "late_minutes": {"$gt": 0},
        }},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1},
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "employee",
        }},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$set": {
            "emp_code": "$_id",
            "name": "$employee.name",
            "department": "$employee.department",
        }},
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": DESCENDING},
            "output": {"rank": {"$rank": {}}},
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": DESCENDING, "emp_code": ASCENDING}},
        {"$project": {
            "_id": 0, "rank": 1, "emp_code": 1, "name": 1, "department": 1,
            "total_late_minutes": 1, "late_count": 1,
        }},
    ])
    return pipeline


@router.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    parse_month(month)
    return {
        "month": month,
        "items": list(db.attendance_logs.aggregate(late_leaderboard_pipeline(month, limit, department))),
    }


def department_trend_pipeline(department: str, from_day: str, to_day: str) -> list[dict]:
    first = parse_date(from_day, "from")
    last = parse_date(to_day, "to")
    days = (last - first).days + 1
    return [
        {"$match": {"department": department}},
        {"$group": {"_id": "$department"}},
        {"$project": {
            "_id": 0,
            "department": "$_id",
            "dates": {"$map": {
                "input": {"$range": [0, days]},
                "as": "offset",
                "in": {"$dateToString": {
                    "date": {"$dateAdd": {
                        "startDate": {"$dateFromString": {"dateString": from_day}},
                        "unit": "day",
                        "amount": "$$offset",
                    }},
                    "format": "%Y-%m-%d",
                }},
            }},
        }},
        {"$unwind": "$dates"},
        {"$set": {"date": "$dates"}},
        {"$lookup": {
            "from": "employees",
            "let": {"dept": "$department", "day": "$date"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$department", "$$dept"]},
                    {"$lte": ["$joined_on", "$$day"]},
                ]}}},
                {"$project": {"_id": 0, "emp_code": 1}},
            ],
            "as": "joined_employees",
        }},
        {"$set": {"headcount": {"$size": "$joined_employees"}}},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"dept": "$department", "day": "$date"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$date", "$$day"]}}},
                {"$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "matched_employee",
                }},
                {"$unwind": "$matched_employee"},
                {"$match": {"$expr": {"$eq": ["$matched_employee.department", "$$dept"]}}},
                {"$project": {
                    "_id": 0, "status": 1, "late_minutes": 1, "half_day": 1,
                }},
            ],
            "as": "day_logs",
        }},
        {"$set": {
            "is_working_day": {"$in": [
                {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                [2, 3, 4, 5, 6],
            ]},
            "present_count": {"$sum": {"$map": {
                "input": "$day_logs",
                "as": "log",
                "in": {"$cond": [
                    {"$in": ["$$log.status", list(PRESENCE_STATUSES)]},
                    {"$cond": [{"$ifNull": ["$$log.half_day", False]}, 0.5, 1]},
                    0,
                ]},
            }}},
            "late_count": {"$size": {"$filter": {
                "input": "$day_logs",
                "as": "log",
                "cond": {"$gt": [{"$ifNull": ["$$log.late_minutes", 0]}, 0]},
            }}},
        }},
        {"$set": {
            "attendance_rate": {"$cond": [
                {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                {"$divide": ["$present_count", "$headcount"]},
                None,
            ]},
        }},
        {"$set": {
            "attendance_rate": round_pipeline("$attendance_rate", 4),
        }},
        {"$setWindowFields": {
            "sortBy": {"date": ASCENDING},
            "output": {
                "moving_avg_raw": {
                    "$avg": "$attendance_rate",
                    "window": {"documents": [-6, 0]},
                },
            },
        }},
        {"$set": {"moving_avg_7d": round_pipeline("$moving_avg_raw", 4)}},
        {"$project": {
            "_id": 0, "date": 1, "is_working_day": 1, "headcount": 1,
            "present_count": 1, "late_count": 1, "attendance_rate": 1,
            "moving_avg_7d": 1,
        }},
        {"$sort": {"date": ASCENDING}},
    ]


@router.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_: str = Query(..., alias="from"), to: str = Query(...)):
    first = parse_date(from_, "from")
    last = parse_date(to, "to")
    if last < first:
        raise HTTPException(422, "to must be on or after from")
    if (last - first).days + 1 > 92:
        raise HTTPException(422, "date range cannot exceed 92 days")
    if db.employees.find_one({"department": department}, {"_id": 1}) is None:
        raise HTTPException(404, "department not found")
    rows = list(db.employees.aggregate(department_trend_pipeline(department, from_, to)))
    return {"department": department, "items": rows}


def explain_command(collection_name: str, pipeline: list[dict]) -> dict:
    return db.command(
        "explain",
        {"aggregate": collection_name, "pipeline": pipeline, "cursor": {}},
        verbosity="executionStats",
    )


@router.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal[
        "attendance_list", "employee_monthly", "department_summary",
        "late_leaderboard", "department_trend",
    ],
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    validate_page(page, page_size)
    if endpoint == "attendance_list":
        query = attendance_query(emp_code, date_from, date_to, status)
        explain = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": DESCENDING, "emp_code": ASCENDING},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            verbosity="executionStats",
        )
        collection_name = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")
        parse_month(month)
        pipeline = employee_monthly_pipeline(emp_code, month)
        explain = explain_command("attendance_logs", pipeline)
        collection_name = "attendance_logs"
    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")
        parse_month(month)
        pipeline = department_summary_pipeline(month, department)
        explain = explain_command("employees", pipeline)
        collection_name = "employees"
    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")
        parse_month(month)
        pipeline = late_leaderboard_pipeline(month, limit, department)
        explain = explain_command("attendance_logs", pipeline)
        collection_name = "attendance_logs"
    else:
        if not department or not from_ or not to:
            raise HTTPException(422, "department, from and to are required")
        first = parse_date(from_, "from")
        last = parse_date(to, "to")
        if last < first or (last - first).days + 1 > 92:
            raise HTTPException(422, "invalid trend date range")
        pipeline = department_trend_pipeline(department, from_, to)
        explain = explain_command("employees", pipeline)
        collection_name = "employees"

    # Convert BSON-only values (for example, ObjectIds in the explain plan) into
    # ordinary JSON-compatible Extended JSON objects for FastAPI's response.
    return {
        "endpoint": endpoint,
        "collection": collection_name,
        "explain": json_util.loads(json_util.dumps(explain)),
    }
