"""接入层：路由聚合。"""

from app.api import auth, catalog, jobs, uploads

__all__ = ["auth", "catalog", "jobs", "uploads"]
