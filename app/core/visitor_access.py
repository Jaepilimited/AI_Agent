"""Shared capability rule for the narrowly scoped visitor analytics view."""


def can_view_visitor_analytics(role: str, explicitly_allowed: bool = False) -> bool:
    """Admins always have access; everyone else needs the Admin-managed AD flag."""
    return role == "admin" or bool(explicitly_allowed)
