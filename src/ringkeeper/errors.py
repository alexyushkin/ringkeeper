class RingkeeperError(Exception):
    """Base for domain errors. The message is safe to show to users."""


class NotFoundError(RingkeeperError):
    pass


class AlreadyExistsError(RingkeeperError):
    pass


class RuleViolationError(RingkeeperError):
    """A registry rule (defaults, prod, bound instances, …) would be broken."""
