# Employee Attendance & Analytics API

A small REST API for managing employee attendance. It uses FastAPI for the HTTP endpoints and MongoDB to store employee and attendance records.

The goal of this project is to keep the rules easy to follow: an employee can punch in, punch out, and have a record corrected when needed. The API also provides a few reports using MongoDB aggregation pipelines.

## What it does

- Create employees and list them, with optional department filtering and pagination.
- Record punch-in and punch-out times.
- Calculate work hours, late minutes, overtime minutes, and half-day status.
- Reject invalid attendance actions, such as creating a second attendance record for the same employee and date.
- Correct an attendance record and keep a history of who made the change, when it was made, and why.
- Return attendance records and analytics for employees and departments.
- Show MongoDB explain details for supported analytics queries.

## How the code is organized

The project is split by responsibility, not into a large number of layers:

```text
app/
├── main.py                 # Creates the FastAPI app and registers routes
├── database.py             # Connects to MongoDB and creates indexes
├── models.py               # Checks request data before route code uses it
├── helpers.py              # Date/time conversion and attendance calculations
├── employee_routes.py      # Create and list employees
├── attendance_routes.py    # Punch in/out, attendance listing, corrections
└── analytics_routes.py     # Reports built with MongoDB aggregation pipelines
```

`main.py` is intentionally small. Each route file handles one area of the API. Shared date/time and calculation code lives in `helpers.py` so the same rules are not copied into every endpoint.

## Request flow

1. FastAPI receives a request.
2. A Pydantic model checks the request body where one is required.
3. The route checks the business rule, such as whether the employee exists or has already punched in.
4. MongoDB reads or updates the record.
5. The API returns a JSON response or a suitable HTTP error.

For reports, the API asks MongoDB to filter, group, sort, and calculate results with aggregation pipelines. This avoids loading every attendance record into Python just to calculate a report.

## Business rules

- Employee codes must follow the format checked in `models.py` and must be unique in MongoDB.
- Punch-in requires an existing employee and creates one attendance record for that employee and attendance date.
- A duplicate record for the same employee and date is rejected.
- Punch-out requires an existing employee and an earlier punch-in. The punch-out must be after punch-in and within 24 hours.
- Attendance calculations are updated when punch-out is recorded.
- A manual correction requires a reason and the name of the person making it. The previous values are retained in the record's history.
- Dates used for attendance are interpreted using the project's India Standard Time (IST) rules. Stored timestamps are handled consistently as UTC instants.

These rules are implemented in the code; if you change a rule, update its tests and documentation too.

## Requirements

- Python 3.11 or newer
- MongoDB 6.0 or newer, either a local instance or MongoDB Atlas
- Git

## Run locally

### 1. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install packages

```bash
pip install -r requirements.txt
```

### 3. Configure MongoDB

Copy `.env.example` to `.env` and set the connection details:

```dotenv
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_test_db
```

For Atlas, use the connection string from your own Atlas database user instead. Allow your current IP in Atlas Network Access. Use a test database while developing. Never commit `.env` or share a connection string containing credentials.

### 4. Start the API

Run this command from the project root, the folder containing `app/` and `requirements.txt`:

```bash
uvicorn app.main:app --reload --port 8000
```

Open the interactive API documentation at <http://127.0.0.1:8000/docs>.

The health endpoint is <http://127.0.0.1:8000/health>. A successful response is `{"status":"ok"}` when MongoDB is reachable.

## Suggested manual test order

Use Swagger UI at `/docs` so you can send JSON request bodies easily.

1. `GET /health` — confirm the app can reach MongoDB.
2. `POST /employees` — create a test employee with a unique employee code.
3. `GET /employees` — confirm the employee appears.
4. `POST /attendance/punch-in` — punch in that employee.
5. `POST /attendance/punch-out` — punch out the same employee.
6. `GET /attendance` — confirm the record and calculated fields.
7. `PATCH /attendance/{emp_code}/{date}` — correct a record using the request schema shown in Swagger, then confirm the history was saved.
8. Try each `/analytics/...` endpoint and compare the output with the sample data or a small known test set.
9. Try invalid cases: unknown employee, duplicate punch-in, punch-out without punch-in, and invalid timestamps.
10. Use `/admin/explain/{endpoint}` to inspect query plans for the supported endpoint names.

Use new employee codes or clear only your dedicated test database between test runs. Do not delete data from a shared or production database.

## Design choices

- **FastAPI and Pydantic:** FastAPI provides the HTTP interface, and Pydantic validates incoming data before business logic runs.
- **MongoDB:** employee and attendance records are stored as documents, which also makes it straightforward to keep correction history with the attendance record.
- **Unique indexes:** application checks provide readable errors, while unique indexes protect against duplicate employee codes and duplicate employee/date records even if two requests arrive close together.
- **Aggregation pipelines:** MongoDB performs the report filtering and grouping close to the stored data.
- **Small modules:** each file has a clear job, without adding a separate service class for every endpoint.
- **Environment variables:** connection details are configuration, not source code.

More specific trade-offs and the starter-code review are recorded in `DECISIONS.md` and `REVIEW.md`.

## Files required for submission

The assignment asks for these files in the public Git repository:

- `app/main.py`
- `requirements.txt`
- `REVIEW.md`
- `DECISIONS.md`
- `README.md`

The additional files under `app/` keep the implementation readable. Do not commit `.env`, passwords, private keys, database exports, or a Dockerfile.

## Before submitting

- Run the API against your own MongoDB test database.
- Test every endpoint, including invalid requests.
- Check the analytics against the assignment's sample data and expected results.
- Review `REVIEW.md` and `DECISIONS.md` and make sure you can explain every point in your own words.
- Check `git status` and confirm `.env` is not staged before pushing.
