from api.routes.agents import router as agents_router
from api.routes.approvals import router as approvals_router
from api.routes.audit import router as audit_router
from api.routes.browsers import router as browsers_router
from api.routes.github import router as github_router
from api.routes.health import router as health_router
from api.routes.incidents import router as incidents_router
from api.routes.inventory import router as inventory_router
from api.routes.notifications import router as notifications_router
from api.routes.overview import router as overview_router
from api.routes.playbooks import router as playbooks_router
from api.routes.probes import router as probes_router
from api.routes.runs import router as runs_router
from api.routes.session import router as session_router
from api.routes.settings import router as settings_router
from api.routes.walkthroughs import router as walkthroughs_router

__all__ = [
    "agents_router",
    "approvals_router",
    "audit_router",
    "browsers_router",
    "github_router",
    "health_router",
    "incidents_router",
    "inventory_router",
    "notifications_router",
    "overview_router",
    "playbooks_router",
    "probes_router",
    "runs_router",
    "session_router",
    "settings_router",
    "walkthroughs_router",
]
