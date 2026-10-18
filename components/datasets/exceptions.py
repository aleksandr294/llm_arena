import uuid


class DatasetNotFoundError(Exception):
    def __init__(self, dataset_id: uuid.UUID) -> None:
        super().__init__(f"Dataset {dataset_id} not found")
        self.dataset_id = dataset_id
