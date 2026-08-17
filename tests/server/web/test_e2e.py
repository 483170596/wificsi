import asyncio

from wificsi.simulator import SimulatorConfig, run_simulator
from wificsi.web.runtime import DashboardRuntime
from wificsi.web.settings import DashboardSettings


NODE_ID = bytes.fromhex("020000000001")


def test_udp_simulator_feeds_observer_hub_and_rate_tracking():
    async def scenario():
        runtime = DashboardRuntime(
            DashboardSettings(udp_host="127.0.0.1", udp_port=0, publish_hz=10.0)
        )
        await runtime.start()
        port = int(runtime.udp_endpoint.split(":")[1])
        try:
            await run_simulator(
                SimulatorConfig(
                    host="127.0.0.1",
                    port=port,
                    node_id=NODE_ID,
                    rate=60.0,
                    duration=0.6,
                    csi_lengths=(64,),
                    hello_interval=0.1,
                    state_interval=0.1,
                    heartbeat_interval=0.1,
                    boot_id=0x10203040,
                )
            )
            await asyncio.sleep(0.05)
            runtime._publish_once()

            assert runtime.hub.latest(NODE_ID) is not None
            assert runtime.registry.get(NODE_ID) is not None
            assert runtime.ingest.observer_errors == 0
            assert runtime.csi_rate_hz(NODE_ID) is not None
        finally:
            await runtime.stop()

    asyncio.run(scenario())
