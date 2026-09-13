"""FastAPI application for agent-facing Ghost workflow memory."""

def create_app(*args, **kwargs):
    # Importing transport models must not create the default SQLite database.
    from .app import create_app as factory

    return factory(*args, **kwargs)

__all__ = ["create_app"]
