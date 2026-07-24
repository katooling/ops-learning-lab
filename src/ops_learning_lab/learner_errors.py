"""Shared learner-history errors without coupling projections to persistence."""

from .storage import StorageError


class LearnerStateError(StorageError):
    """Raised when durable learner state is unsafe, corrupt, or stale."""
