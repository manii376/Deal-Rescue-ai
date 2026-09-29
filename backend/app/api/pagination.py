from dataclasses import dataclass
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

T = TypeVar("T")

MAX_LIMIT = 200


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


def page_params(
    limit: int = Query(50, ge=1, le=MAX_LIMIT, description="Maximum items to return"),
    offset: int = Query(0, ge=0, le=1_000_000, description="Items to skip"),
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


def paginate(session: Session, statement: SelectOfScalar, params: PageParams) -> tuple[list, int]:
    total = session.exec(select(func.count()).select_from(statement.order_by(None).subquery())).one()
    items = list(session.exec(statement.limit(params.limit).offset(params.offset)).all())
    return items, total
