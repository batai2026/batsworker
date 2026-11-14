"""Per-domain API routers for the sidecar.

`coworker/server/app.py` used to hold every route in one `create_app` closure (217 of
them). Each module here owns one domain and exposes a `build_*_router(manager, ...)`
factory returning an `APIRouter`; `create_app` includes them in the original registration
order.

A factory rather than `Depends(get_manager)` on purpose: the handlers were moved out
verbatim, still closing over `manager`, so every extraction stays a pure move with no
signature churn. `tests/test_route_inventory.py` pins the resulting surface (paths,
methods, endpoint names, order).
"""
