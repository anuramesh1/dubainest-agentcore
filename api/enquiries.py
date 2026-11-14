"""POST /api/enquiries: the detail page's enquiry form, saved to the Leads table.

dubainest-ai kept enquiries in process memory, so they were lost on restart and
never reached Leads. Here each one is a Leads item (spec §7), keyed by leadId,
findable per listing through the table's ByProperty index (propertyId, createdAt).
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
    table = boto3.resource("dynamodb", region_name=settings.region()).Table(
        settings.leads_table()
    )
    table.put_item(
        Item={
            "leadId": record.id,
            "propertyId": record.property_id,
            "createdAt": record.created_at.isoformat(),
            "source": "enquiry_form",
            "name": record.name,
            "email": record.email,
            "phone": record.phone,
            "message": record.message,
        },
        ConditionExpression="attribute_not_exists(leadId)",
    )
    # Contact details stay out of the logs.
    log.info("lead saved lead_id=%s property_id=%s", record.id, record.property_id)
    return record
