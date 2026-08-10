from app.api.routes.approvals import router as approvals_router
from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.leads import router as leads_router
from app.api.routes.products import router as products_router
from app.api.routes.whatsapp_cloud import router as whatsapp_cloud_router
from app.api.routes.workflows import router as workflows_router
from app.api.routes.workspaces import router as workspaces_router

__all__ = [
    "approvals_router",
    "auth_router",
    "conversations_router",
    "integrations_router",
    "leads_router",
    "products_router",
    "whatsapp_cloud_router",
    "workflows_router",
    "workspaces_router",
]
