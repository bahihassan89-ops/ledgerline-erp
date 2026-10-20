"""Per-request context (current user, company, IP) available to services and the audit log."""
import contextvars
from dataclasses import dataclass, field


@dataclass
class RequestContext:
    user: object = None
    actor_label: str = ""
    company_id: int | None = None
    ip: str | None = None
    user_agent: str = ""
    request_id: str = ""
    extra: dict = field(default_factory=dict)


_ctx: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar("erp_ctx", default=None)


def get() -> RequestContext:
    ctx = _ctx.get()
    if ctx is None:  # outside a request (management command, worker): own context, never shared
        ctx = RequestContext()
        _ctx.set(ctx)
    return ctx


def set_context(ctx: RequestContext):
    return _ctx.set(ctx)


def reset(token):
    _ctx.reset(token)
