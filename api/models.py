"""Response and request shapes. They must match frontend/src/app/models (spec §2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Property(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    titleAr: str  # noqa: N815 (field names match the UI's Property interface)
    area: str
    areaAr: str  # noqa: N815
    price: int
    currency: str
    type: Literal["apartment", "villa", "townhouse", "penthouse", "studio"]
    bedrooms: int
    bathrooms: int
    sizeSqft: int  # noqa: N815
    images: list[str]
    description: str
    descriptionAr: str  # noqa: N815
    amenities: list[str]
    roi: float
    developer: str
    completionYear: int  # noqa: N815
    lat: float
    lng: float


class Facets(BaseModel):
    areas: list[str]
    developers: list[str]
    types: list[str]
    bedrooms: list[int]


class EnquiryCreate(BaseModel):
    """The enquiry form on the detail page. Limits keep a public endpoint cheap to abuse-proof."""

    property_id: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200)
    phone: str = Field(min_length=3, max_length=40)
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("name", "email", "phone", "message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("email")
    @classmethod
    def looks_like_email(cls, v: str) -> str:
        local, _, domain = v.partition("@")
        if not local or "." not in domain:
            raise ValueError("not a valid email address")
        return v


class EnquiryRead(EnquiryCreate):
    id: str
    created_at: datetime


class PropertyIntro(BaseModel):
    intro: str
    suggestions: list[str]
