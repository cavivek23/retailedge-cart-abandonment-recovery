import logging


logger = logging.getLogger("retailedge.inventory.audit")


def audit_inventory_event(
    event: str,
    *,
    role: str,
    operation_id: str,
    old_version: int | None = None,
    new_version: int | None = None,
) -> None:
    """Emit structured operational metadata without tenant or customer identifiers."""
    logger.info(
        "inventory_event",
        extra={
            "event": event,
            "operation_id": operation_id,
            "role": role,
            "old_version": old_version,
            "new_version": new_version,
        },
    )