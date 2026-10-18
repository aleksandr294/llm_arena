import uuid


class ExperimentNotFoundError(Exception):
    def __init__(self, experiment_id: uuid.UUID) -> None:
        super().__init__(f"Experiment {experiment_id} not found")
        self.experiment_id = experiment_id


class InvalidTransitionError(Exception):
    def __init__(self, experiment_id: uuid.UUID, status: str, action: str):
        super().__init__(f"Cannot {action} an experiment that is {status}")
        self.experiment_id = experiment_id
        self.status = status
        self.action = action


class ExperimentValidationError(Exception):
    """Input that passes schema validation but cannot be accepted (422).
    ``field`` names the offending request field; the message never contains
    secret values."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message
