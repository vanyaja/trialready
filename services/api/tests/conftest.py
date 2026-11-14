import asyncio
import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="trialready-test-")
os.environ.update({"DATABASE_URL": f"sqlite:///{_tmp}/test.db", "MOCK_JEV": "true", "SIM_RING_SECONDS": "0.3", "ENABLE_SCHEDULER": "false",
                   "ALLOW_LIVE_GEOCODE": "false", "DEMO_MODE": "false", "TOOL_SHARED_SECRET": "", "ELEVENLABS_API_KEY": "", "ELEVENLABS_WEBHOOK_SECRET": ""})

from services.api.config import get_settings  # noqa: E402

get_settings.cache_clear()

from services.api import db as dbmod  # noqa: E402
from services.api.jev.client import JevRouter, set_router  # noqa: E402
from services.api.seed import seed  # noqa: E402


@pytest.fixture(scope="session")
def seeded():
    dbmod.reset_engine()
    set_router(JevRouter(force_mock=True))
    return asyncio.run(seed(reset=True))


@pytest.fixture()
def run():
    return lambda coro: asyncio.run(coro)
