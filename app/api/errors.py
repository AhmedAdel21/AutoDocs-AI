from fastapi import HTTPException, status
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class APIError(HTTPException):
    """Base API error. Always returns the consistent envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
    ):
        super().__init__(
            status_code=status_code,
            detail={"error": {"code": code, "message": message, "details": details}},
        )


class NotFoundError(APIError):
    def __init__(self, resource: str, id_: str):
        super().__init__(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            f"{resource} not found",
            {"id": id_},
        )


class ConflictError(APIError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(status.HTTP_409_CONFLICT, "conflict", message, details)


class BadRequestError(APIError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(status.HTTP_400_BAD_REQUEST, "bad_request", message, details)
