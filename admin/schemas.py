from components.core.schemas import Principal


class SessionUser:
    """What the user admin returns while fastadmin validates a session.

    fastadmin binds the value as ``self.user``. It is deliberately not a
    serialisable type (no ``__dict__``, not a model or dataclass): were it
    ever returned from a data route, serialising it fails instead of
    exposing the principal's ids and role.
    """

    __slots__ = ("principal",)

    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    def __repr__(self) -> str:
        return "SessionUser()"
