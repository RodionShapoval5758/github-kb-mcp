from mcp.server.mcpserver.exceptions import ToolError


# Subclassing ToolError makes the SDK show the message to Claude; other exceptions become a generic "Error executing tool".
class KBError(ToolError):
    pass


class InvalidPathError(KBError):
    pass


class KBNotFoundError(KBError):
    pass


class KBUnavailableError(KBError):
    pass
