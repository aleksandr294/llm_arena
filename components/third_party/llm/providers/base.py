from abc import ABC, abstractmethod

from components.third_party.llm.enums import ProviderName
from components.third_party.llm.schemas import (
    LLMRequestSchema,
    LLMResponseSchema,
)


class BaseLLMProvider(ABC):
    name: ProviderName

    @abstractmethod
    async def generate(self, request: LLMRequestSchema) -> LLMResponseSchema:
        """Send the request; raise ``LLMProviderError`` on any failure."""
