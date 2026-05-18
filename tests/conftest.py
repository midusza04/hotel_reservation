"""
Shared pytest fixtures.

Ray is started in local mode (no cluster) for all tests so they run
without Docker.  DB calls inside actors are mocked with a no-op patch
so no Postgres is required either.
"""

import sys
import os
import types
import pytest

# ---------------------------------------------------------------------------
# Make `app/` importable from `tests/`
# ---------------------------------------------------------------------------
APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, os.path.abspath(APP_DIR))


# ---------------------------------------------------------------------------
# Mock db module BEFORE actors.py is imported so no DB connection is made
# ---------------------------------------------------------------------------
def _make_db_mock():
    db = types.ModuleType("db")
    db.init_db = lambda: None
    db.load_hotels = lambda: []
    db.load_reservations = lambda: []
    db.save_hotel_snapshot = lambda **kw: None
    db.save_reservation = lambda r: None
    db.update_reservation_status = lambda *a, **kw: None
    db.write_audit_log = lambda e: None
    db.load_audit_logs = lambda **kw: []
    return db


sys.modules.setdefault("db", _make_db_mock())


# ---------------------------------------------------------------------------
# Ray: start once in local mode for the whole test session
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def ray_local():
    import ray
    if not ray.is_initialized():
        # If RAY_ADDRESS is set (inside the Docker cluster), connect without
        # specifying resources.  Otherwise start a local Ray instance for
        # running tests outside Docker.
        ray_address = os.environ.get("RAY_ADDRESS")
        if ray_address:
            ray.init(address=ray_address, ignore_reinit_error=True)
        else:
            ray.init(num_cpus=2, ignore_reinit_error=True)
    yield
    # Don't shut down Ray if we connected to an existing cluster
    if not os.environ.get("RAY_ADDRESS"):
        ray.shutdown()
