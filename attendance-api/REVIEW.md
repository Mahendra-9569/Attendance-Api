# Code Review

- Validate request data at the API boundary so invalid values are rejected consistently.
- Use a unique index for employee codes and for one attendance record per employee and date.
- Keep manual corrections auditable by storing who made the correction, why, when, and what changed.
- Check concurrent updates before writing so one request does not silently overwrite another.
- Use MongoDB aggregation pipelines for reporting calculations rather than loading all records into Python.
- Verify time-zone handling and boundary cases with tests before release.
