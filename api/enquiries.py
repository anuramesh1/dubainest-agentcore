"""POST /api/enquiries: the detail page's enquiry form, saved to the Leads table.

Each enquiry is a Leads item keyed by leadId, with propertyId and createdAt, so
enquiries survive restarts and sit next to the chat's confirmed viewings.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

import boto3
from fastapi import APIRouter, HTTPException

from . import settings
from .models import EnquiryCreate, EnquiryRead
from .properties import CATALOGUE

router = APIRouter(prefix="/api/enquiries", tags=["enquiries"])
log = logging.getLogger(__name__)


@router.post("", response_model=EnquiryRead, status_code=201)
def create_enquiry(enquiry: EnquiryCreate) -> EnquiryRead:
    if CATALOGUE.get(enquiry.property_id) is None:
        raise HTTPException(status_code=404, detail="Property not found")

    record = EnquiryRead(id=str(uuid4()), created_at=datetime.now(UTC), **enquiry.model_dump())
    table = boto3.resource("dynamodb", region_name=settings.region()).Table(settings.leads_table())
    table.put_item(
        Item={
            "leadId": record.id,
            "propertyId": record.property_id,
            "createdAt": record.created_at.isoformat(),
            "source": "enquiry_form",
            "name": record.name,
            "email": record.email,
            # optional fields are stored only when given
            **({"phone": record.phone} if record.phone else {}),
            **({"message": record.message} if record.message else {}),
        },
        ConditionExpression="attribute_not_exists(leadId)",
    )
    # Contact details stay out of the logs.
    log.info("lead saved lead_id=%s property_id=%s", record.id, record.property_id)
    return record
