import asyncio
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("agent.py")
SPEC = importlib.util.spec_from_file_location("factory_agent", MODULE_PATH)
agent = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(agent)


class FakeResponse:
    def __init__(self, status_code=200, text="", json_value=None):
        self.status_code = status_code
        self.text = text
        self._json_value = json_value

    def json(self):
        return self._json_value


class FakeHttpClient:
    def __init__(self, robot_data, post_status=200, block_posts=False):
        self.robot_data = robot_data
        self.post_status = post_status
        self.posts = []
        self.block_posts = block_posts
        self.post_started = asyncio.Event()
        self.release_post = asyncio.Event()

    async def get(self, url):
        robot_id = next((key for key in self.robot_data if key in url), None)
        if robot_id is None:
            return FakeResponse(404)
        data = self.robot_data[robot_id]
        if url.endswith("/SupportedCapabilities"):
            return FakeResponse(json_value={"value": data["routes"]})
        if url.endswith("/IsMoving"):
            return FakeResponse(text=json.dumps(data.get("moving", False)))
        if url.endswith("/FaultActive"):
            return FakeResponse(text=json.dumps(data.get("fault", False)))
        return FakeResponse(404)

    async def post(self, url, json):
        self.posts.append((url, json))
        self.post_started.set()
        if self.block_posts:
            await self.release_post.wait()
        return FakeResponse(status_code=self.post_status)

    async def aclose(self):
        pass


def route(
    station_id,
    sensor="Sensor_BoxPresent",
    operation="ExecuteMoveBox",
    source="Conveyor1",
    target="Pallet1",
    route_id="Route_01",
    extra=None,
):
    values = [
        {"idShort": "StationId", "valueType": "xs:string", "value": station_id},
        {"idShort": "TriggerSensor", "valueType": "xs:string", "value": sensor},
        {"idShort": "TargetOperation", "valueType": "xs:string", "value": operation},
    ]
    if source is not None:
        values.append({"idShort": "SourcePosition", "valueType": "xs:string", "value": source})
    if target is not None:
        values.append({"idShort": "TargetPosition", "valueType": "xs:string", "value": target})
    values.extend(extra or [])
    return {"idShort": route_id, "value": values}


class FactoryOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        temp_path = Path(self.tempdir.name)
        config = agent.AgentConfig(
            station_registry_file="",
            job_timeout_seconds=30,
            invoke_retry_count=1,
            orchestrator_log_csv_path=str(temp_path / "runs.csv"),
            orchestrator_summary_csv_path=str(temp_path / "summary.csv"),
        )
        self.orchestrator = agent.FactoryOrchestrator(config)
        await self.orchestrator.http_client.aclose()

    async def asyncTearDown(self):
        await self.orchestrator.close()
        self.tempdir.cleanup()

    def configure(self, robots, robot_data, post_status=200, block_posts=False):
        self.orchestrator.robots = robots
        fake_client = FakeHttpClient(robot_data, post_status, block_posts)
        self.orchestrator.http_client = fake_client
        return fake_client

    def make_job(self, station_id="Station_01", token="job-1", sensor_key="sensor-1"):
        return {
            "station_id": station_id,
            "sensor": "Sensor_BoxPresent",
            "sensor_key": sensor_key,
            "token": token,
            "t1_ms": 1,
            "deadline": time.monotonic() - 1,
            "required_operation": "ExecuteMoveBox",
        }

    async def test_cross_station_route_overrides_robot_home_station(self):
        robot = agent.RobotEndpoints("state-r2", "skills-r2", "Station_02")
        self.configure(
            [robot],
            {
                "skills-r2": {"routes": [route("Station_01", source="S1", target="T1")]},
                "state-r2": {"routes": [], "moving": False, "fault": False},
            },
        )

        await self.orchestrator.process_factory_job(self.make_job())

        dispatch = self.orchestrator.dispatch_queue.get_nowait()
        self.assertEqual(dispatch["robot_key"], "state-r2")
        self.assertEqual(dispatch["station_id"], "Station_01")
        self.assertEqual(dispatch["source_position"], "S1")
        self.assertEqual(dispatch["target_position"], "T1")
        self.assertEqual(
            dispatch["operation_inputs"],
            {"StationId": "Station_01", "SourcePosition": "S1", "TargetPosition": "T1"},
        )
        id_shorts = [item["value"]["idShort"] for item in dispatch["body"]["inputArguments"]]
        self.assertEqual(
            id_shorts[:3],
            ["StationId", "SourcePosition", "TargetPosition"],
        )
        self.assertNotIn("TargetPosition.", id_shorts)

    async def test_robot_without_requested_station_route_is_rejected(self):
        robot = agent.RobotEndpoints("state-r2", "skills-r2", "Station_02")
        self.configure(
            [robot],
            {
                "skills-r2": {"routes": [route("Station_02")]},
                "state-r2": {"routes": [], "moving": False, "fault": False},
            },
        )

        await self.orchestrator.process_factory_job(self.make_job())

        self.assertTrue(self.orchestrator.dispatch_queue.empty())
        self.assertNotIn("state-r2", self.orchestrator.reserved_robots)

    async def test_route_without_station_is_rejected(self):
        robot = agent.RobotEndpoints("state-r1", "skills-r1", "Station_01")
        self.configure(
            [robot],
            {
                "skills-r1": {"routes": [route("")]},
                "state-r1": {"routes": [], "moving": False, "fault": False},
            },
        )

        await self.orchestrator.process_factory_job(self.make_job())

        self.assertTrue(self.orchestrator.dispatch_queue.empty())

    async def test_moving_or_faulted_robot_is_rejected(self):
        for moving, fault in ((True, False), (False, True)):
            with self.subTest(moving=moving, fault=fault):
                robot = agent.RobotEndpoints("state-r1", "skills-r1", "Station_01")
                self.configure(
                    [robot],
                    {
                        "skills-r1": {"routes": [route("Station_01")]},
                        "state-r1": {"routes": [], "moving": moving, "fault": fault},
                    },
                )
                await self.orchestrator.process_factory_job(
                    self.make_job(token=f"job-{moving}-{fault}")
                )
                self.assertTrue(self.orchestrator.dispatch_queue.empty())
                self.assertFalse(self.orchestrator.reserved_robots)

    async def test_simultaneous_jobs_cannot_reserve_same_robot(self):
        robot = agent.RobotEndpoints("state-r1", "skills-r1", "Station_01")
        self.configure(
            [robot],
            {
                "skills-r1": {"routes": [route("Station_01")]},
                "state-r1": {"routes": [], "moving": False, "fault": False},
            },
        )

        await asyncio.gather(
            self.orchestrator.process_factory_job(self.make_job(token="job-a")),
            self.orchestrator.process_factory_job(self.make_job(token="job-b")),
        )

        self.assertEqual(self.orchestrator.dispatch_queue.qsize(), 1)
        self.assertEqual(self.orchestrator.reserved_robots, {"state-r1"})

    async def test_reservation_released_after_success_and_failure(self):
        for status_code in (200, 400):
            with self.subTest(status_code=status_code):
                robot = agent.RobotEndpoints("state-r1", "skills-r1", "Station_01")
                fake = self.configure(
                    [robot],
                    {
                        "skills-r1": {"routes": [route("Station_01")]},
                        "state-r1": {"routes": [], "moving": False, "fault": False},
                    },
                    post_status=status_code,
                )
                job = self.make_job(token=f"job-{status_code}", sensor_key=f"sensor-{status_code}")
                self.orchestrator.sensor_states[job["sensor_key"]] = False
                await self.orchestrator.process_factory_job(job)
                dispatch = self.orchestrator.dispatch_queue.get_nowait()
                await self.orchestrator.dispatch_factory_job(dispatch)

                self.assertEqual(len(fake.posts), 1)
                if status_code == 200:
                    await self.orchestrator.handle_operation_ack(
                        json.dumps(
                            {
                                "requestId": dispatch["request_id"],
                                "stationId": "Station_01",
                                "status": "completed",
                            }
                        )
                    )

                self.assertNotIn("state-r1", self.orchestrator.reserved_robots)
                self.assertNotIn("station_01", self.orchestrator.reserved_stations)

    async def test_reservation_released_when_dispatch_is_cancelled(self):
        robot = agent.RobotEndpoints("state-r1", "skills-r1", "Station_01")
        fake = self.configure(
            [robot],
            {
                "skills-r1": {"routes": [route("Station_01")]},
                "state-r1": {"routes": [], "moving": False, "fault": False},
            },
            block_posts=True,
        )
        await self.orchestrator.process_factory_job(self.make_job())
        dispatch = self.orchestrator.dispatch_queue.get_nowait()
        task = asyncio.create_task(self.orchestrator.dispatch_factory_job(dispatch))
        await fake.post_started.wait()

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertNotIn("state-r1", self.orchestrator.reserved_robots)
        self.assertNotIn("station_01", self.orchestrator.reserved_stations)

    async def test_existing_robot01_and_robot02_station_routes_still_select(self):
        robots = [
            agent.RobotEndpoints("state-r1", "skills-r1", "Station_01"),
            agent.RobotEndpoints("state-r2", "skills-r2", "Station_02"),
        ]
        self.configure(
            robots,
            {
                "skills-r1": {"routes": [route("Station_01")]},
                "state-r1": {"routes": [], "moving": False, "fault": False},
                "skills-r2": {"routes": [route("Station_02")]},
                "state-r2": {"routes": [], "moving": False, "fault": False},
            },
        )

        await self.orchestrator.process_factory_job(
            self.make_job(station_id="Station_02")
        )

        dispatch = self.orchestrator.dispatch_queue.get_nowait()
        self.assertEqual(dispatch["robot_key"], "state-r2")
        self.assertEqual(dispatch["station_id"], "Station_02")

    async def test_canonical_registry_loads_station_03_without_code_changes(self):
        registry_path = Path(self.tempdir.name) / "stations.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "stations": {
                        "station_03": {
                            "stationId": "Station_03",
                            "conveyorSubmodelB64": "conveyor-state-3",
                            "conveyorOperationsSubmodelB64": "conveyor-ops-3",
                            "robotStateSubmodelB64": "robot-state-3",
                            "robotSkillsSubmodelB64": "robot-skills-3",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        temp_path = Path(self.tempdir.name)
        config = agent.AgentConfig(
            station_registry_file=str(registry_path),
            orchestrator_log_csv_path=str(temp_path / "registry-runs.csv"),
            orchestrator_summary_csv_path=str(temp_path / "registry-summary.csv"),
        )
        orchestrator = agent.FactoryOrchestrator(config)
        try:
            self.assertEqual(
                orchestrator.station_by_conveyor_submodel,
                {"conveyor-state-3": "Station_03"},
            )
            self.assertEqual(
                orchestrator.robots,
                [
                    agent.RobotEndpoints(
                        "robot-state-3",
                        "robot-skills-3",
                        "Station_03",
                    )
                ],
            )
        finally:
            await orchestrator.close()

    def test_move_to_home_retains_fixed_home_arguments(self):
        parsed = self.orchestrator._parse_capability_route(
            route(
                "Station_01",
                sensor="Sensor_ClearRobot",
                operation="ExecuteMoveToHome",
                source=None,
                target=None,
                extra=[{"idShort": "move", "valueType": "xs:boolean", "value": True}],
            )
        )

        inputs = self.orchestrator._build_operation_inputs(parsed)

        self.assertEqual(inputs, {"move": {"value": True, "valueType": "xs:boolean"}})
        self.assertNotIn("StationId", inputs)
        self.assertNotIn("SourcePosition", inputs)
        self.assertNotIn("TargetPosition", inputs)


if __name__ == "__main__":
    unittest.main()
