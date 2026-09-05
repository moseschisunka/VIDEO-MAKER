"""Library live updates must stream, respect scope, and release subscribers."""

import asyncio
import json

import pytest
from fastapi.responses import StreamingResponse

from backlot import server


@pytest.mark.asyncio
async def test_library_stream_filters_scope_and_cleans_up(monkeypatch):
    monkeypatch.setenv("BACKLOT_PROJECT_SCOPE", "school-a-*")
    hub = server.ChangeHub()
    monkeypatch.setattr(server, "hub", hub)
    endpoint = next(
        route.endpoint for route in server.create_app().routes
        if route.path == "/api/library/events"
    )

    class Request:
        async def is_disconnected(self):
            return False

    response = await endpoint(Request())
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    stream = response.body_iterator
    try:
        assert '"hello"' in await anext(stream)
        assert hub.subscriber_count() == 1
        hub.publish("school-b-private")
        hub.publish("school-a-lesson")
        event = await asyncio.wait_for(anext(stream), timeout=1)
        assert json.loads(event.removeprefix("data: ").strip()) == {
            "type": "change", "project_id": "school-a-lesson",
        }
    finally:
        await stream.aclose()
    assert hub.subscriber_count() == 0
