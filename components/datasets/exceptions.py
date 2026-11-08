import uuid


class DatasetFullError(Exception):
    def __init__(self, dataset_id: uuid.UUID, limit: int) -> None:
        super().__init__(f"Dataset already holds the maximum of {limit} cases")
        self.dataset_id = dataset_id
        self.limit = limit


class DatasetNotFoundError(Exception):
    def __init__(self, dataset_id: uuid.UUID) -> None:
        super().__init__(f"Dataset {dataset_id} not found")
        self.dataset_id = dataset_id
