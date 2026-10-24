"""Tenant-scoped procurement requests, supplier quotes, and award workflow."""
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_membership
from app.audit import record_audit_event
from app.db import get_db
from app.models import (
    Membership,
    ProcurementRequestRecord,
    PurchaseOrder,
    SupplierProfile,
    SupplierQuote,
    utc_now,
)
from app.schemas import (
    ProcurementAwardRequest,
    ProcurementRequestCreate,
    PurchaseOrderResponse,
    ProcurementRequestResponse,
    SupplierQuoteCreate,
    SupplierQuoteResponse,
)

router = APIRouter(tags=["procurement"])


def _require_owner_or_admin(membership: Membership) -> None:
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")


@router.post(
    "/organizations/{organization_id}/procurement-requests",
    response_model=ProcurementRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_procurement_request(
    payload: ProcurementRequestCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> ProcurementRequestResponse:
    _require_owner_or_admin(membership)
    request = ProcurementRequestRecord(
        id=str(uuid4()),
        buyer_organization_id=membership.organization_id,
        created_by_user_id=membership.user_id,
        title=payload.title,
        description=payload.description.strip(),
        quantity=payload.quantity,
        currency=payload.currency,
        status="open",
    )
    db.add(request)
    db.flush()
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="procurement_request.created",
        entity_type="procurement_request",
        entity_id=request.id,
        details={"status": request.status, "quantity": request.quantity, "currency": request.currency},
    )
    db.commit()
    db.refresh(request)
    return ProcurementRequestResponse.model_validate(request)


@router.get(
    "/organizations/{organization_id}/procurement-requests",
    response_model=list[ProcurementRequestResponse],
)
def list_procurement_requests(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
    request_status: Annotated[str | None, Query(alias="status", pattern="^(open|awarded|closed)$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProcurementRequestResponse]:
    _require_owner_or_admin(membership)
    query = select(ProcurementRequestRecord).where(
        ProcurementRequestRecord.buyer_organization_id == membership.organization_id
    )
    if request_status is not None:
        query = query.where(ProcurementRequestRecord.status == request_status)
    rows = db.scalars(
        query.order_by(ProcurementRequestRecord.created_at.desc(), ProcurementRequestRecord.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [ProcurementRequestResponse.model_validate(row) for row in rows]


@router.get(
    "/organizations/{organization_id}/procurement-requests/{request_id}/quotes",
    response_model=list[SupplierQuoteResponse],
)
def list_procurement_quotes(
    request_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> list[SupplierQuoteResponse]:
    _require_owner_or_admin(membership)
    request = db.scalar(
        select(ProcurementRequestRecord).where(
            ProcurementRequestRecord.id == request_id,
            ProcurementRequestRecord.buyer_organization_id == membership.organization_id,
        )
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Procurement request not found")
    quotes = db.scalars(
        select(SupplierQuote)
        .where(SupplierQuote.procurement_request_id == request.id)
        .order_by(SupplierQuote.unit_price_minor, SupplierQuote.lead_time_days, SupplierQuote.created_at)
    ).all()
    return [SupplierQuoteResponse.model_validate(quote) for quote in quotes]


@router.post(
    "/organizations/{organization_id}/procurement-requests/{request_id}/quotes",
    response_model=SupplierQuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_supplier_quote(
    request_id: str,
    payload: SupplierQuoteCreate,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> SupplierQuoteResponse:
    _require_owner_or_admin(membership)
    profile = db.scalar(
        select(SupplierProfile).where(
            SupplierProfile.organization_id == membership.organization_id,
            SupplierProfile.status == "approved",
        )
    )
    if profile is None:
        raise HTTPException(status_code=403, detail="An approved supplier profile is required")
    request = db.scalar(
        select(ProcurementRequestRecord).where(ProcurementRequestRecord.id == request_id).with_for_update()
    )
    if request is None or request.status != "open":
        raise HTTPException(status_code=404, detail="Open procurement request not found")
    if request.buyer_organization_id == membership.organization_id:
        raise HTTPException(status_code=409, detail="A buyer organization cannot quote on its own request")
    if payload.currency != request.currency:
        raise HTTPException(status_code=422, detail="Quote currency must match the procurement request")
    quote = SupplierQuote(
        id=str(uuid4()),
        procurement_request_id=request.id,
        supplier_organization_id=membership.organization_id,
        created_by_user_id=membership.user_id,
        unit_price_minor=payload.unit_price_minor,
        currency=payload.currency,
        lead_time_days=payload.lead_time_days,
        notes=payload.notes.strip(),
        status="submitted",
    )
    db.add(quote)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This supplier has already quoted on the request") from None
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="supplier_quote.submitted",
        entity_type="supplier_quote",
        entity_id=quote.id,
        details={"status": quote.status, "price_minor": quote.unit_price_minor, "currency": quote.currency},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This supplier has already quoted on the request") from None
    db.refresh(quote)
    return SupplierQuoteResponse.model_validate(quote)


@router.post(
    "/organizations/{organization_id}/procurement-requests/{request_id}/award",
    response_model=ProcurementRequestResponse,
)
def award_procurement_request(
    request_id: str,
    payload: ProcurementAwardRequest,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> ProcurementRequestResponse:
    _require_owner_or_admin(membership)
    request = db.scalar(
        select(ProcurementRequestRecord)
        .where(
            ProcurementRequestRecord.id == request_id,
            ProcurementRequestRecord.buyer_organization_id == membership.organization_id,
        )
        .with_for_update()
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Procurement request not found")
    if request.status != "open":
        raise HTTPException(status_code=409, detail="Only open procurement requests can be awarded")
    quote = db.scalar(
        select(SupplierQuote).where(
            SupplierQuote.id == payload.quote_id,
            SupplierQuote.procurement_request_id == request.id,
            SupplierQuote.status == "submitted",
        )
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="Submitted quote not found for this request")
    now = utc_now()
    transitioned = db.execute(
        update(ProcurementRequestRecord)
        .where(
            ProcurementRequestRecord.id == request.id,
            ProcurementRequestRecord.status == "open",
        )
        .values(status="awarded", awarded_quote_id=quote.id, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if transitioned.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Procurement request was already closed or awarded")
    quote.status = "accepted"
    quote.updated_at = now
    db.execute(
        update(SupplierQuote)
        .where(
            SupplierQuote.procurement_request_id == request.id,
            SupplierQuote.id != quote.id,
            SupplierQuote.status == "submitted",
        )
        .values(status="rejected", updated_at=now)
    )
    request.status = "awarded"
    request.awarded_quote_id = quote.id
    request.updated_at = now
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="procurement_request.awarded",
        entity_type="procurement_request",
        entity_id=request.id,
        details={"status": request.status},
    )
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=quote.supplier_organization_id,
        action="supplier_quote.accepted",
        entity_type="supplier_quote",
        entity_id=quote.id,
        details={"status": quote.status, "price_minor": quote.unit_price_minor, "currency": quote.currency},
    )
    db.commit()
    db.refresh(request)
    return ProcurementRequestResponse.model_validate(request)




@router.post(
    "/organizations/{organization_id}/procurement-requests/{request_id}/purchase-order",
    response_model=PurchaseOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_purchase_order(
    request_id: str,
    response: Response,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> PurchaseOrderResponse:
    """Issue one immutable purchase-order snapshot from the awarded quote."""
    _require_owner_or_admin(membership)
    request = db.scalar(
        select(ProcurementRequestRecord).where(
            ProcurementRequestRecord.id == request_id,
            ProcurementRequestRecord.buyer_organization_id == membership.organization_id,
        )
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Procurement request not found")
    existing = db.scalar(
        select(PurchaseOrder).where(PurchaseOrder.procurement_request_id == request.id)
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return PurchaseOrderResponse.model_validate(existing)
    if request.status != "awarded" or request.awarded_quote_id is None:
        raise HTTPException(status_code=409, detail="Only an awarded procurement request can issue a purchase order")
    quote = db.scalar(
        select(SupplierQuote).where(
            SupplierQuote.id == request.awarded_quote_id,
            SupplierQuote.procurement_request_id == request.id,
            SupplierQuote.status == "accepted",
        )
    )
    if quote is None:
        raise HTTPException(status_code=409, detail="The awarded supplier quote is unavailable")
    total_minor = request.quantity * quote.unit_price_minor
    if total_minor <= 0 or total_minor > 9_000_000_000_000_000:
        raise HTTPException(status_code=422, detail="Purchase-order total exceeds the supported monetary limit")

    now = utc_now()
    order = PurchaseOrder(
        id=str(uuid4()),
        procurement_request_id=request.id,
        supplier_quote_id=quote.id,
        buyer_organization_id=request.buyer_organization_id,
        supplier_organization_id=quote.supplier_organization_id,
        quantity=request.quantity,
        unit_price_minor=quote.unit_price_minor,
        total_minor=total_minor,
        currency=request.currency,
        status="issued",
        created_by_user_id=membership.user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(order)
    db.flush()
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=request.buyer_organization_id,
        action="purchase_order.issued",
        entity_type="purchase_order",
        entity_id=order.id,
        details={"total_minor": total_minor, "currency": order.currency, "status": order.status},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(PurchaseOrder).where(PurchaseOrder.procurement_request_id == request.id)
        )
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return PurchaseOrderResponse.model_validate(existing)
        raise HTTPException(status_code=409, detail="Purchase order could not be issued") from None
    db.refresh(order)
    return PurchaseOrderResponse.model_validate(order)


@router.get(
    "/organizations/{organization_id}/purchase-orders",
    response_model=list[PurchaseOrderResponse],
)
def list_purchase_orders(
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PurchaseOrderResponse]:
    _require_owner_or_admin(membership)
    orders = db.scalars(
        select(PurchaseOrder)
        .where(
            or_(
                PurchaseOrder.buyer_organization_id == membership.organization_id,
                PurchaseOrder.supplier_organization_id == membership.organization_id,
            )
        )
        .order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [PurchaseOrderResponse.model_validate(order) for order in orders]


@router.post(
    "/organizations/{organization_id}/purchase-orders/{purchase_order_id}/fulfill",
    response_model=PurchaseOrderResponse,
)
def fulfill_purchase_order(
    purchase_order_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> PurchaseOrderResponse:
    _require_owner_or_admin(membership)
    order = db.scalar(
        select(PurchaseOrder).where(
            PurchaseOrder.id == purchase_order_id,
            PurchaseOrder.supplier_organization_id == membership.organization_id,
        )
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    if order.status == "fulfilled":
        return PurchaseOrderResponse.model_validate(order)
    if order.status != "issued":
        raise HTTPException(status_code=409, detail="Only issued purchase orders can be fulfilled")
    now = utc_now()
    transitioned = db.execute(
        update(PurchaseOrder)
        .where(PurchaseOrder.id == order.id, PurchaseOrder.status == "issued")
        .values(status="fulfilled", updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if transitioned.rowcount != 1:
        db.rollback()
        current = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == purchase_order_id))
        if current is not None and current.status == "fulfilled":
            return PurchaseOrderResponse.model_validate(current)
        raise HTTPException(status_code=409, detail="Purchase order is no longer available for fulfilment")
    order.status = "fulfilled"
    order.updated_at = now
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="purchase_order.fulfilled",
        entity_type="purchase_order",
        entity_id=order.id,
        details={"status": order.status},
    )
    db.commit()
    db.refresh(order)
    return PurchaseOrderResponse.model_validate(order)


@router.post(
    "/organizations/{organization_id}/purchase-orders/{purchase_order_id}/cancel",
    response_model=PurchaseOrderResponse,
)
def cancel_purchase_order(
    purchase_order_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> PurchaseOrderResponse:
    _require_owner_or_admin(membership)
    order = db.scalar(
        select(PurchaseOrder).where(
            PurchaseOrder.id == purchase_order_id,
            PurchaseOrder.buyer_organization_id == membership.organization_id,
        )
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    if order.status == "cancelled":
        return PurchaseOrderResponse.model_validate(order)
    if order.status != "issued":
        raise HTTPException(status_code=409, detail="Only issued purchase orders can be cancelled")
    now = utc_now()
    transitioned = db.execute(
        update(PurchaseOrder)
        .where(PurchaseOrder.id == order.id, PurchaseOrder.status == "issued")
        .values(status="cancelled", updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if transitioned.rowcount != 1:
        db.rollback()
        current = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == purchase_order_id))
        if current is not None and current.status == "cancelled":
            return PurchaseOrderResponse.model_validate(current)
        raise HTTPException(status_code=409, detail="Purchase order is no longer available for cancellation")
    order.status = "cancelled"
    order.updated_at = now
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="purchase_order.cancelled",
        entity_type="purchase_order",
        entity_id=order.id,
        details={"status": order.status},
    )
    db.commit()
    db.refresh(order)
    return PurchaseOrderResponse.model_validate(order)


@router.post(
    "/organizations/{organization_id}/procurement-requests/{request_id}/close",
    response_model=ProcurementRequestResponse,
)
def close_procurement_request(
    request_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> ProcurementRequestResponse:
    _require_owner_or_admin(membership)
    request = db.scalar(
        select(ProcurementRequestRecord).where(
            ProcurementRequestRecord.id == request_id,
            ProcurementRequestRecord.buyer_organization_id == membership.organization_id,
        )
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Procurement request not found")
    if request.status == "awarded":
        raise HTTPException(status_code=409, detail="An awarded request cannot be closed through this endpoint")
    if request.status == "closed":
        return ProcurementRequestResponse.model_validate(request)
    now = utc_now()
    transitioned = db.execute(
        update(ProcurementRequestRecord)
        .where(
            ProcurementRequestRecord.id == request.id,
            ProcurementRequestRecord.status == "open",
        )
        .values(status="closed", updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if transitioned.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Procurement request was already closed or awarded")
    request.status = "closed"
    request.updated_at = now
    db.execute(
        update(SupplierQuote)
        .where(
            SupplierQuote.procurement_request_id == request.id,
            SupplierQuote.status == "submitted",
        )
        .values(status="rejected", updated_at=utc_now())
    )
    record_audit_event(
        db,
        actor_user_id=membership.user_id,
        organization_id=membership.organization_id,
        action="procurement_request.closed",
        entity_type="procurement_request",
        entity_id=request.id,
        details={"status": request.status},
    )
    db.commit()
    db.refresh(request)
    return ProcurementRequestResponse.model_validate(request)
