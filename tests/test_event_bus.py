from amora.events import AmoraEvent, AsyncEventBus


async def test_event_bus_deduplicates_same_key() -> None:
    bus = AsyncEventBus()
    received: list[str] = []
    async def handler(event: AmoraEvent) -> None:
        received.append(event.event_type)
    await bus.subscribe("test.event", handler)
    first = AmoraEvent(event_type="test.event", source="pytest", dedupe_key="same")
    second = AmoraEvent(event_type="test.event", source="pytest", dedupe_key="same")
    result_a = await bus.publish(first)
    result_b = await bus.publish(second)
    assert result_a.deduplicated is False
    assert result_b.deduplicated is True
    assert received == ["test.event"]
