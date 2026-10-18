from enum import StrEnum


class ProviderName(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class LLMErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    SERVER_ERROR = "server_error"
    MALFORMED_RESPONSE = "malformed_response"
    UNKNOWN = "unknown"
