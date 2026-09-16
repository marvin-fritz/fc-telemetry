"""fc_telemetry – gemeinsame Telemetrie der Finanz-Copilot-Dienste."""

from .context import active_jobs, current_job, job_context, job_error_state, record_job_error

__version__ = "0.1.0"

__all__ = ["__version__", "job_context", "current_job", "active_jobs", "record_job_error", "job_error_state"]
