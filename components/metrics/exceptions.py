class PricingConfigError(Exception):
    """The configured pricing file is missing or invalid."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"Pricing file {path!r} is unusable: {reason}")
        self.path = path
        self.reason = reason
