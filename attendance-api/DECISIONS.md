# Design Decisions

- FastAPI provides request parsing, validation, HTTP status codes, and generated Swagger documentation.
- PyMongo is used directly to keep database access explicit and avoid an additional ORM layer.
- Request models are kept in `models.py`; reusable date/time and attendance calculations live in `helpers.py`.
- Routes are grouped by behavior to make the code easier to navigate and test.
- MongoDB unique indexes protect important uniqueness rules even when requests arrive concurrently.
- Attendance timestamps are stored as datetimes in MongoDB and exposed as epoch milliseconds by the API.
- The `.env` file is local configuration and must never be committed.
