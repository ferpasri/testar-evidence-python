"""Errors reported while reading, validating, or saving evidence."""


class EvidenceError(ValueError):
    """A stable error code with a readable explanation and optional field."""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        self.code = code
        self.field = field
        super().__init__(message)
