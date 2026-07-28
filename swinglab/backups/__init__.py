"""Inactive-by-default backup and scratch-restore support.

The web application does not import or schedule this package. Operators must
invoke the explicit CLI and enable its environment gate.
"""

from .core import BackupError, create_backup, restore_backup

__all__ = ["BackupError", "create_backup", "restore_backup"]
