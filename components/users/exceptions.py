class InvalidEmailError(ValueError):
    pass


class DuplicateEmailError(Exception):
    def __init__(self) -> None:
        super().__init__("a user with this email already exists")


class UserForbiddenError(Exception):
    def __init__(self) -> None:
        super().__init__("forbidden")


class UserNotFoundError(Exception):
    """Also raised for users outside the caller's scope, so their
    existence is not disclosed."""

    def __init__(self) -> None:
        super().__init__("user not found")


class UserConflictError(Exception):
    """The change would lock the caller out of their own account."""


class UserInputError(ValueError):
    """Invalid input; ``field`` names the offending request field."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
