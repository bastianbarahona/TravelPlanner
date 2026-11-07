class ApiError(Exception):
    """Every error the API sends. main.py turns it into the provider's Error shape.

    ponytail: carries an HTTP status, so the domain knows about HTTP. One mapping, no second
    exception hierarchy, until a non-HTTP caller (the MCP server, Iteration 6) needs the services.
    """

    def __init__(self, status, code, message, details=None, headers=None):
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


class ValidationFailed(ApiError):
    def __init__(self, errors):
        super().__init__(422, "VALIDATION_FAILED", "The request is invalid.", {"errors": errors})


def not_found(what="Resource"):
    # One message for a missing trip, a nonparticipant, a malformed id and an unknown route (main.py): none may be told apart.
    return ApiError(404, "NOT_FOUND", f"{what} not found.")
