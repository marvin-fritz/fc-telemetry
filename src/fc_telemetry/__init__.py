"""fc_telemetry – gemeinsame Telemetrie der Finanz-Copilot-Dienste."""

from .context import active_jobs, current_job, job_context, job_error_state, record_job_error
from .logging_setup import JsonFormatter, LogCounterHandler, TextFormatter, setup_logging
from .mongo_activity import MongoActivity, install_mongo_activity

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "job_context",
    "current_job",
    "active_jobs",
    "record_job_error",
    "job_error_state",
    "JsonFormatter",
    "TextFormatter",
    "LogCounterHandler",
    "setup_logging",
    "MongoActivity",
    "install_mongo_activity",
]
