"""The job kinds and dedupe keys the review selection shares between the API (manual runs) and
the worker (the nightly job)."""

import uuid
from datetime import date

SELECT_JOB = "select_for_review"
SELECT_PROJECT_JOB = "select_for_review_project"


def project_job_key(project_id: uuid.UUID, day: date) -> str:
    """One selection job per project and day, whoever asks for it."""
    return f"{SELECT_PROJECT_JOB}:{project_id}:{day.isoformat()}"
