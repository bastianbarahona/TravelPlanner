"""The id of the request (or background poll) being served, for error bodies and the provider-call log line."""
from contextvars import ContextVar

# ponytail: never reset. uvicorn runs each request in its own task, so nothing leaks, and the 500 handler (outside
# the middleware) still sees it. Reset in a finally if a worker ever serves two requests in one context.
request_id = ContextVar("request_id", default=None)
