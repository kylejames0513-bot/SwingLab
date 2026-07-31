"""Stable Shopify boundary with compatibility-backed implementations.

The legacy implementations remain under :mod:`swinglab.web`, but importing an
identity helper must not import the whole web layer (or create a circular import
while the user store is loading).  The compatibility aliases are therefore
resolved lazily and remain identical to their legacy modules for callers.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from .identity import customer_gid, normalize_customer_id

if TYPE_CHECKING:
    from ...web import shop as storefront
    from ...web import shopify_billing as webhooks

__all__ = [
    "customer_gid",
    "normalize_customer_id",
    "storefront",
    "webhooks",
]


def __getattr__(name: str):
    if name == "storefront":
        module = import_module("swinglab.web.shop")
    elif name == "webhooks":
        module = import_module("swinglab.web.shopify_billing")
    else:
        raise AttributeError(name)
    globals()[name] = module
    return module


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
