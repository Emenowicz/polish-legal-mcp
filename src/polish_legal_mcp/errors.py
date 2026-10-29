"""Domain exceptions. Messages are Polish because they reach the MCP client verbatim."""


class LegalMcpError(Exception):
    """Base class for errors that may be shown to the MCP client."""


class InvalidInputError(LegalMcpError):
    """User input could not be interpreted (e.g. malformed act identifier)."""


class UpstreamNotFoundError(LegalMcpError):
    """Upstream answered 404. Not proof of nonexistence across all sources and versions."""


class UpstreamUnavailableError(LegalMcpError):
    """Upstream timed out, rate-limited us, or failed with 5xx after retries."""


class UpstreamBadResponseError(LegalMcpError):
    """Upstream answered with something we cannot use (4xx, malformed, oversized)."""
