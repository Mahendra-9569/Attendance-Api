from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from app.database import db
from app.helpers import *
from app.models import EmployeeCreate, PunchInRequest, PunchOutRequest, RegularizeRequest

router = APIRouter()


@router.post("/employees", status_code=201)
def create_employee(body: EmployeeCreate):
    doc = body.model_dump()
    doc["created_at"] = now_utc_seconds()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return public_employee(doc)


@router.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {"department": department} if department is not None else {}
    total = db.employees.count_documents(query)
    docs = db.employees.find(query, {"_id": 0}).sort("emp_code", ASCENDING).skip(
        (page - 1) * page_size
    ).limit(page_size)
    return {
        "items": [public_employee(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
