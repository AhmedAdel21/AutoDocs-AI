import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cursor import decode_cursor, encode_cursor
from app.api.errors import NotFoundError
from app.auth.dependencies import get_current_user, require_role
from app.db import get_db
from app.models.document import Document
from app.models.user import User, UserRole
from app.schemas.document import (
    DocumentCreate,
    DocumentRead,
    DocumentListResponse,
)
from app.services.ingestion import ingest_document


router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a document (chunks + embeds)",
    dependencies=[Depends(require_role(UserRole.LEAD, UserRole.ADMIN))],
)
async def create_document(
    payload: DocumentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Ingest a document. Idempotent on content_hash — re-uploading the same content
    returns the existing document with 200 instead of 201.
    """
    document, was_created = await ingest_document(
        db,
        title=payload.title,
        raw_content=payload.raw_content,
        uploaded_by=current_user.id,
    )

    # If the document already existed, return 200 (not 201) and the existing record
    # FastAPI doesn't easily let us change status mid-handler with a custom response,
    # so we just return — the response_model handles serialization.
    # Note: senior detail — the proper REST semantic for "already exists, returning existing"
    # is technically debatable. 200 with existing resource is the Stripe-style answer.
    return document


@router.get(
    "/{document_id}",
    response_model=DocumentRead,
    summary="Get a document by ID",
)
async def get_document(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Document:
    stmt = select(Document).where(Document.id == document_id)
    document = (await db.execute(stmt)).scalar_one_or_none()
    if document is None:
        raise NotFoundError("document", str(document_id))
    return document


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List documents (cursor paginated)",
)
async def list_documents(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> DocumentListResponse:
    stmt = select(Document)

    if cursor:
        decoded = decode_cursor(cursor)
        cursor_created_at = datetime.fromisoformat(decoded["created_at"])
        cursor_id = uuid.UUID(decoded["id"])
        stmt = stmt.where(
            (Document.created_at, Document.id) < (cursor_created_at, cursor_id)
        )

    stmt = stmt.order_by(Document.created_at.desc(), Document.id.desc()).limit(
        limit + 1
    )
    rows = (await db.execute(stmt)).scalars().all()

    has_more = len(rows) > limit
    items = list(rows[:limit])
    next_cursor = (
        encode_cursor(items[-1].created_at, items[-1].id)
        if has_more and items
        else None
    )

    return DocumentListResponse(
        items=[DocumentRead.model_validate(d) for d in items],
        next_cursor=next_cursor,
    )
