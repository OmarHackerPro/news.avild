from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str = Field(json_schema_extra={"example": "Resource not found"})


class ValidationErrorResponse(BaseModel):
    detail: list[dict] = Field(
        json_schema_extra={
            "example": [
                {
                    "loc": ["body", "email"],
                    "msg": "value is not a valid email address",
                    "type": "value_error",
                }
            ]
        }
    )
