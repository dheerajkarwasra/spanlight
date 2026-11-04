"""Evidence for one logical span write, retained across every transaction attempt."""

import uuid
from dataclasses import dataclass

import structlog

from app.gateway.span_losses import lose

logger = structlog.get_logger(__name__)


@dataclass
class WriteEvidence:
    unacknowledged_commit: bool = False
    stored: bool = False
    counted: bool = False
    reported: bool = False

    def abandoned(self, project_id: uuid.UUID) -> bool:
        """Account once after the writer joins; return whether its storage is known."""
        if self.stored or self.counted:
            return True
        if self.unacknowledged_commit:
            if not self.reported:
                logger.warning("gateway.record_shutdown_uncertain", reason="unacknowledged_commit")
                logger.warning(
                    "process.shutdown_incomplete",
                    phase="accounting",
                    reason="unacknowledged_commit",
                    accounting_complete=False,
                )
                self.reported = True
            return False
        lose(project_id, "error")
        self.counted = True
        return True
