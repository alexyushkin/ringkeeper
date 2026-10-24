class InterlockError(Exception):
    """Base for domain errors. The message is safe to show to users."""


class NotFoundError(InterlockError):
    pass


class AlreadyExistsError(InterlockError):
    pass


class RuleViolationError(InterlockError):
    """A registry rule (defaults, prod, bound instances, …) would be broken."""
