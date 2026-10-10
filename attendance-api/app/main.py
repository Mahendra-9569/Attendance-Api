from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pymongo.errors import PyMongoError
from app.database import client, create_indexes, db
from app.employee_routes import router as employee_router
from app.attendance_routes import router as attendance_router
from app.analytics_routes import router as analytics_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        create_indexes()
    except PyMongoError:
        pass
    yield
    client.close()

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)
app.include_router(employee_router)
app.include_router(attendance_router)
app.include_router(analytics_router)

@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "MongoDB is unavailable")
