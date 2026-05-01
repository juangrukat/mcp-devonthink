"""Shared live DEVONthink fixture UUIDs for integration tests."""

from __future__ import annotations

import pytest


DB_INBOX_UUID = "0444C204-D8AD-4CC0-8A9A-9F6817C12896"
SCHOLAR_CORPUS_GROUP_UUID = "180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"
CHAOS_LAB_GROUP_UUID = "3EFAAB4A-5BCD-4699-A472-1F66EF3C7882"
CHAOS_LAB_GROUP_NAME = "MCP Chaos Lab 20260424-080344"


@pytest.fixture(scope="session")
def devonthink_live_fixture_uuids() -> dict[str, str]:
    return {
        "database_inbox": DB_INBOX_UUID,
        "scholar_corpus_group": SCHOLAR_CORPUS_GROUP_UUID,
        "chaos_lab_group": CHAOS_LAB_GROUP_UUID,
        "chaos_lab_name": CHAOS_LAB_GROUP_NAME,
    }
