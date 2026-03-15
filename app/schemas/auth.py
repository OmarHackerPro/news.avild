from pydantic import BaseModel, EmailStr, Field, field_validator


class SignupRequest(BaseModel):
    email: EmailStr = Field(json_schema_extra={"example": "analyst@example.com"})
    password: str = Field(json_schema_extra={"example": "S3cur3P@ss!"})
    name: str = Field(json_schema_extra={"example": "Jane Doe"})

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name must not be empty")
        return v.strip()


class LoginRequest(BaseModel):
    email: EmailStr = Field(json_schema_extra={"example": "analyst@example.com"})
    password: str = Field(json_schema_extra={"example": "S3cur3P@ss!"})


class ForgotPasswordRequest(BaseModel):
    email: EmailStr = Field(json_schema_extra={"example": "analyst@example.com"})


class ResetPasswordRequest(BaseModel):
    token: str = Field(json_schema_extra={"example": "abc123def456..."})
    new_password: str = Field(json_schema_extra={"example": "N3wS3cur3P@ss!"})

    @field_validator("new_password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class ProfileUpdateRequest(BaseModel):
    name: str | None = Field(None, json_schema_extra={"example": "Jane Smith"})
    new_password: str | None = Field(None, json_schema_extra={"example": "Upd@t3dP@ss!"})

    @field_validator("new_password")
    @classmethod
    def password_min_length(cls, v: str | None) -> str | None:
        if v is not None and len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UserResponse(BaseModel):
    id: int = Field(json_schema_extra={"example": 1})
    email: str = Field(json_schema_extra={"example": "analyst@example.com"})
    name: str = Field(json_schema_extra={"example": "Jane Doe"})
    profile_picture: str | None = Field(None, json_schema_extra={"example": "uploads/avatars/user_1_a3b4c5d6.jpg"})

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    access_token: str = Field(json_schema_extra={"example": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."})
    token_type: str = Field("bearer", json_schema_extra={"example": "bearer"})
    user: UserResponse
