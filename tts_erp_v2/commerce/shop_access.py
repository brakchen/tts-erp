"""The authorization requirement shared by business queries and scheduling."""

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session


def authorized_shop_clause(alias: str = "commerce.shops") -> str:
    """SQL using a source-owned table alias, never a request parameter."""
    return (
        f"({alias}.platform <> 'tiktok' OR EXISTS ("
        "SELECT 1 FROM integration.credentials shop_credential "
        f"WHERE shop_credential.id = {alias}.credential_id "
        "AND shop_credential.provider = 'tiktok' "
        f"AND shop_credential.external_account_id = {alias}.shop_id "
        f"AND shop_credential.service_id = {alias}.service_id "
        "AND EXISTS (SELECT 1 FROM integration.tiktok_app_credentials shop_app "
        f"WHERE shop_app.shop_pk = {alias}.id "
        f"AND shop_app.service_id = {alias}.service_id "
        "AND (shop_credential.extra->>'app_revision' IS NULL "
        "OR shop_credential.extra->>'app_revision' = shop_app.revision::text))))"
    )


def require_authorized_shop(
    session: Session, *, shop_pk: int | None = None, shop_id: str | None = None
) -> None:
    condition = "s.id = :shop_pk" if shop_pk is not None else "s.platform = 'tiktok' AND s.shop_id = :shop_id"
    row = session.execute(
        text("SELECT 1 FROM commerce.shops s WHERE " + condition + " AND " + authorized_shop_clause("s")),
        {"shop_pk": shop_pk, "shop_id": shop_id},
    ).first()
    if row is None:
        raise HTTPException(404, "Shop is not authorized")
