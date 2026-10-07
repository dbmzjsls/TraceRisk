class PermanentWorkflowError(ValueError):
    """Terminal workflow failures are audited without queue-level retries."""
