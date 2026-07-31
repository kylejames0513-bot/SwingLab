"""Stable Shopify boundary with compatibility-backed implementations.

The implementation modules remain under :mod:`swinglab.web` in this foundation
release to preserve every existing import. Future moves can occur behind these
module aliases.
"""

from ...web import shop as storefront
from ...web import shopify_billing as webhooks

__all__ = ["storefront", "webhooks"]
