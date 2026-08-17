import asyncio

from wificsi.web.connections import ClientMailbox, ConnectionManager


def test_mailbox_merge_on_latest():
    async def scenario():
        mailbox = ClientMailbox()
        mailbox.offer_latest(("csi_batch", "a"), {"n": 1})
        mailbox.offer_latest(("csi_batch", "a"), {"n": 2})
        mailbox.offer_latest(("node_status", "a"), {"s": 1})
        events = await mailbox.next_event()
        assert len(events) == 2
        csi = next(e for e in events if "n" in e)
        assert csi["n"] == 2
        assert mailbox.coalesced == 1

    asyncio.run(scenario())


def test_mailbox_close_wakes_next_event():
    async def scenario():
        mailbox = ClientMailbox()
        mailbox.close()
        events = await mailbox.next_event()
        assert events == []

    asyncio.run(scenario())


def test_connection_manager_respects_subscription():
    manager = ConnectionManager()
    conn = manager.add()
    node_a = bytes.fromhex("020000000001")
    node_b = bytes.fromhex("020000000002")
    manager.set_subscription(conn, {node_a})

    manager.publish("csi_batch", node_a, {"node": "a"})
    manager.publish("csi_batch", node_b, {"node": "b"})

    assert conn.mailbox.pending == 1


def test_connection_manager_snapshot_reaches_all():
    manager = ConnectionManager()
    conn = manager.add()
    manager.publish("nodes_snapshot", None, {"nodes": []})
    assert conn.mailbox.pending == 1
