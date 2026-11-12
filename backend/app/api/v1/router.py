from fastapi import APIRouter

from app.api.v1 import (
    account,
    accounts,
    analytics,
    auth,
    balances,
    everyday,
    finance,
    fx,
    health,
    installments,
    links,
    privacy,
    recurring,
    transfers,
    trip_planning,
    trip_reports,
    trips,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(account.router)
api_router.include_router(privacy.router)
api_router.include_router(accounts.router)
api_router.include_router(accounts.currencies_router)
api_router.include_router(transfers.router)
api_router.include_router(finance.router)
api_router.include_router(fx.router)
api_router.include_router(balances.router)
api_router.include_router(recurring.router)
api_router.include_router(installments.router)
api_router.include_router(analytics.router)
api_router.include_router(trips.router)
api_router.include_router(links.router)
api_router.include_router(everyday.router)
api_router.include_router(trip_reports.router)
api_router.include_router(trip_planning.router)
