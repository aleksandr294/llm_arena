class OrganizationRequiredError(Exception):
    """Creating data needs an organization, which superadmins do not have."""

    def __init__(self) -> None:
        super().__init__(
            "A superadmin has no organization and cannot own data; "
            "use an organization account"
        )
