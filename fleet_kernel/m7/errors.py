"""Dependency-free M7 errors used by legacy rollback paths."""


class RoleQueueMigrationError(RuntimeError):
    pass


class RoleQueueShadowLagError(RoleQueueMigrationError):
    """The live append/cursor is newer than the last exact shadow snapshot."""


class RoleQueueParityError(RoleQueueMigrationError):
    """Current shadow evidence exists but its independent proofs disagree."""
