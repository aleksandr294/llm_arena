class OrganizationNotFoundError(Exception):
    def __init__(self) -> None:
        super().__init__("organization not found")


class DuplicateOrganizationNameError(Exception):
    def __init__(self) -> None:
        super().__init__("an organization with this name already exists")
