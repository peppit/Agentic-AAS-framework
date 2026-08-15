import asyncio
import csv
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

from capability_matching import build_operation_inputs, parse_capability_route


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
        self.gets = []
        self.block_posts = block_posts
        self.post_started = asyncio.Event()
        self.release_post = asyncio.Event()

    async def get(self, url):
        self.gets.append(url)
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
        self.log_path = temp_path / "runs.csv"
        config = agent.AgentConfig(
            station_registry_file="",
            job_timeout_seconds=30,
            queue_timeout_seconds=30,
            operation_timeout_seconds=30,
            invoke_retry_count=1,
            orchestrator_log_csv_path=str(self.log_path),
            orchestrator_summary_csv_path=str(temp_path / "summary.csv"),
            measurement_run_id="run-test",
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
        self.orchestrator.server_online = True
        self.orchestrator.server_instance_id = "test-server"
        self.orchestrator.station_statuses[agent.normalize_station_id(station_id)] = {
            "stationId": station_id,
            "online": True,
            "robotReady": True,
            "serverInstanceId": "test-server",
        }
        return {
            "station_id": station_id,
            "sensor": "Sensor_BoxPresent",
            "sensor_key": sensor_key,
            "token": token,
            "t1_ms": 1,
            "deadline": time.monotonic() - 1,
            "required_operation": "ExecuteMoveBox",
        }

    async def test_job_waits_until_station_is_ready(self):
        job = self.make_job()
        job["deadline"] = time.monotonic() + 30
        self.orchestrator.server_online = False
        self.orchestrator.station_statuses.clear()

        await self.orchestrator.process_factory_job(job)

        self.assertEqual(
            list(self.orchestrator.pending_by_station["station_01"]),
            [job],
        )
        await self.orchestrator.handle_station_status(
            "simulation/Station_01/status",
            json.dumps(
                {
                    "stationId": "Station_01",
                    "online": True,
                    "robotReady": True,
                    "serverInstanceId": "new-server",
                }
            ),
        )
        self.assertTrue(self.orchestrator.job_queue.empty())

        await self.orchestrator.handle_server_status(
            json.dumps(
                {
                    "online": True,
                    "serverInstanceId": "new-server",
                }
            )
        )

        self.assertIs(self.orchestrator.job_queue.get_nowait(), job)
        self.assertNotIn("station_01", self.orchestrator.pending_by_station)

    async def test_started_job_is_not_retried_when_station_disconnects(self):
        robot = agent.RobotEndpoints("state-r1", "skills-r1", "Station_01")
        self.configure(
            [robot],
            {
                "skills-r1": {"routes": [route("Station_01")]},
                "state-r1": {"routes": [], "moving": False, "fault": False},
            },
        )
        await self.orchestrator.process_factory_job(self.make_job())
        dispatch = self.orchestrator.dispatch_queue.get_nowait()
        await self.orchestrator.lifecycle.begin_dispatch(dispatch)
        await self.orchestrator.handle_operation_ack(
            json.dumps(
                {
                    "requestId": dispatch["request_id"],
                    "stationId": "Station_01",
                    "status": "started",
                }
            )
        )

        await self.orchestrator.handle_station_status(
            "simulation/Station_01/status",
            json.dumps(
                {
                    "stationId": "Station_01",
                    "online": True,
                    "robotReady": False,
                    "serverInstanceId": "test-server",
                }
            ),
        )

        self.assertNotIn(dispatch["request_id"], self.orchestrator.lifecycle_by_request_id)
        self.assertFalse(self.orchestrator.pending_by_station)
        self.assertNotIn("state-r1", self.orchestrator.reserved_robots)

    async def test_unstarted_job_is_requeued_when_station_disconnects(self):
        robot = agent.RobotEndpoints("state-r1", "skills-r1", "Station_01")
        self.configure(
            [robot],
            {
                "skills-r1": {"routes": [route("Station_01")]},
                "state-r1": {"routes": [], "moving": False, "fault": False},
            },
        )
        await self.orchestrator.process_factory_job(self.make_job())
        dispatch = self.orchestrator.dispatch_queue.get_nowait()
        await self.orchestrator.lifecycle.begin_dispatch(dispatch)

        await self.orchestrator.handle_station_status(
            "simulation/Station_01/status",
            json.dumps(
                {
                    "stationId": "Station_01",
                    "online": True,
                    "robotReady": False,
                    "serverInstanceId": "test-server",
                }
            ),
        )

        self.assertEqual(
            len(self.orchestrator.pending_by_station["station_01"]),
            1,
        )
        await self.orchestrator.handle_station_status(
            "simulation/Station_01/status",
            json.dumps(
                {
                    "stationId": "Station_01",
                    "online": True,
                    "robotReady": True,
                    "serverInstanceId": "test-server",
                }
            ),
        )

        requeued = self.orchestrator.job_queue.get_nowait()
        self.assertEqual(requeued["request_id"], dispatch["request_id"])

    async def test_cross_station_route_overrides_robot_physical_station(self):
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
        self.assertIn("requestId", id_shorts)
        self.assertIn("runId", id_shorts)
        self.assertEqual(dispatch["run_id"], "run-test")

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

    async def test_fault_state_publishes_transitions_only(self):
        robot = agent.RobotEndpoints("state-r1", "skills-r1", "Station_01")
        published = []

        async def publisher(topic, payload):
            published.append((topic, json.loads(payload)))

        self.orchestrator.fault_state_publisher = publisher
        await self.orchestrator._publish_robot_fault_state(robot, True, 1000)
        await self.orchestrator._publish_robot_fault_state(robot, True, 2000)
        await self.orchestrator._publish_robot_fault_state(robot, False, 3000)

        self.assertEqual(len(published), 2)
        self.assertEqual(published[0][0], "factory/robots/state-r1/fault")
        self.assertTrue(published[0][1]["faultActive"])
        self.assertFalse(published[1][1]["faultActive"])
        self.assertEqual(published[1][1]["observedAtMs"], 3000)

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

    async def test_sensor_rearms_before_previous_operation_completes(self):
        self.orchestrator.station_by_conveyor_submodel = {
            "conveyor-1": "Station_01"
        }

        await self.orchestrator.handle_event(
            "conveyor-1",
            "Sensor_BoxPresent",
            "true",
            "topic",
            1,
        )
        first_job = self.orchestrator.job_queue.get_nowait()
        await self.orchestrator.handle_event(
            "conveyor-1",
            "Sensor_BoxPresent",
            "false",
            "topic",
            2,
        )
        await self.orchestrator.handle_event(
            "conveyor-1",
            "Sensor_BoxPresent",
            "true",
            "topic",
            3,
        )
        second_job = self.orchestrator.job_queue.get_nowait()

        self.assertTrue(first_job["sensor_clear"])
        self.assertFalse(second_job["sensor_clear"])
        self.assertNotEqual(first_job["token"], second_job["token"])
        self.assertEqual(len(self.orchestrator.active_jobs), 2)

    async def test_only_capable_busy_robot_gets_fifo_pending_job(self):
        robot = agent.RobotEndpoints("state-r1", "skills-r1", "Station_01")
        self.configure(
            [robot],
            {
                "skills-r1": {"routes": [route("Station_01")]},
                "state-r1": {"routes": [], "moving": True, "fault": False},
            },
        )
        job = self.make_job()
        job["deadline"] = time.monotonic() + 30

        await self.orchestrator.process_factory_job(job)

        self.assertEqual(
            list(self.orchestrator.pending_by_robot["state-r1"]),
            [job],
        )
        self.assertTrue(self.orchestrator.dispatch_queue.empty())

        self.orchestrator.reserved_robots.discard("state-r1")
        await self.orchestrator._schedule_next_for_robot("state-r1")

        dispatch = self.orchestrator.dispatch_queue.get_nowait()
        self.assertEqual(dispatch["request_id"], job["request_id"])
        self.assertEqual(dispatch["robot_key"], "state-r1")

    async def test_idle_capable_robot_is_selected_over_busy_capable_robot(self):
        robots = [
            agent.RobotEndpoints("state-r1", "skills-r1", "Station_01"),
            agent.RobotEndpoints("state-r2", "skills-r2", "Station_02"),
        ]
        self.configure(
            robots,
            {
                "skills-r1": {"routes": [route("Station_01")]},
                "state-r1": {"routes": [], "moving": True, "fault": False},
                "skills-r2": {"routes": [route("Station_01")]},
                "state-r2": {"routes": [], "moving": False, "fault": False},
            },
        )

        await self.orchestrator.process_factory_job(self.make_job())

        dispatch = self.orchestrator.dispatch_queue.get_nowait()
        self.assertEqual(dispatch["robot_key"], "state-r2")
        self.assertFalse(self.orchestrator.pending_unassigned)

    async def test_search_stops_after_first_idle_capable_robot(self):
        robots = [
            agent.RobotEndpoints("state-r1", "skills-r1", "Station_01"),
            agent.RobotEndpoints("state-r2", "skills-r2", "Station_02"),
        ]
        fake = self.configure(
            robots,
            {
                "skills-r1": {"routes": [route("Station_01")]},
                "state-r1": {"routes": [], "moving": False, "fault": False},
                "skills-r2": {"routes": [route("Station_01")]},
                "state-r2": {"routes": [], "moving": False, "fault": False},
            },
        )

        await self.orchestrator.process_factory_job(self.make_job())

        dispatch = self.orchestrator.dispatch_queue.get_nowait()
        self.assertEqual(dispatch["robot_key"], "state-r1")
        self.assertFalse(any("skills-r2" in url or "state-r2" in url for url in fake.gets))

    async def test_multiple_busy_capable_robots_use_shared_pending_queue(self):
        robots = [
            agent.RobotEndpoints("state-r1", "skills-r1", "Station_01"),
            agent.RobotEndpoints("state-r2", "skills-r2", "Station_02"),
        ]
        self.configure(
            robots,
            {
                "skills-r1": {"routes": [route("Station_01")]},
                "state-r1": {"routes": [], "moving": True, "fault": False},
                "skills-r2": {"routes": [route("Station_01")]},
                "state-r2": {"routes": [], "moving": True, "fault": False},
            },
        )
        job = self.make_job()
        job["deadline"] = time.monotonic() + 30

        await self.orchestrator.process_factory_job(job)

        self.assertEqual(list(self.orchestrator.pending_unassigned), [job])
        await self.orchestrator._schedule_next_for_robot("state-r2")

        dispatch = self.orchestrator.dispatch_queue.get_nowait()
        self.assertEqual(dispatch["robot_key"], "state-r2")
        self.assertFalse(self.orchestrator.pending_unassigned)

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
                with self.log_path.open(
                    "r",
                    newline="",
                    encoding="utf-8",
                ) as log_file:
                    matching_rows = [
                        row
                        for row in csv.DictReader(log_file)
                        if row["request_id"] == dispatch["request_id"]
                    ]
                self.assertEqual(len(matching_rows), 1)
                self.assertEqual(matching_rows[0]["run_id"], "run-test")
                self.assertEqual(
                    matching_rows[0]["status"],
                    "ok" if status_code == 200 else "failed",
                )
                self.assertLessEqual(
                    int(matching_rows[0]["t1_ms"]),
                    int(matching_rows[0]["t2_ms"]),
                )
                self.assertLessEqual(
                    int(matching_rows[0]["t2_ms"]),
                    int(matching_rows[0]["t3_ms"]),
                )
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
                        "Robot_03",
                    )
                ],
            )
        finally:
            await orchestrator.close()

    async def test_v2_registry_separates_assets_and_supports_shared_station(self):
        registry_path = Path(self.tempdir.name) / "assets.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "2.0",
                    "stations": {
                        "station_01": {"stationId": "Station_01"},
                        "station_02": {"stationId": "Station_02"},
                    },
                    "robots": {
                        "robot_01": {
                            "robotId": "Robot_01",
                            "stateSubmodelB64": "robot-state-1",
                            "skillsSubmodelB64": "robot-skills-1",
                        },
                        "robot_02": {
                            "robotId": "Robot_02",
                            "stateSubmodelB64": "robot-state-2",
                            "skillsSubmodelB64": "robot-skills-2",
                        },
                    },
                    "conveyors": {
                        "conveyor_01": {
                            "conveyorId": "Conveyor_01",
                            "stateSubmodelB64": "conveyor-state-1",
                        },
                        "conveyor_02": {
                            "conveyorId": "Conveyor_02",
                            "stateSubmodelB64": "conveyor-state-2",
                        },
                    },
                    "stationAssets": [
                        {
                            "stationId": "Station_01",
                            "assetType": "robot",
                            "assetId": "Robot_01",
                        },
                        {
                            "stationId": "Station_01",
                            "assetType": "robot",
                            "assetId": "Robot_02",
                        },
                        {
                            "stationId": "Station_01",
                            "assetType": "conveyor",
                            "assetId": "Conveyor_01",
                        },
                        {
                            "stationId": "Station_02",
                            "assetType": "conveyor",
                            "assetId": "Conveyor_02",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        temp_path = Path(self.tempdir.name)
        orchestrator = agent.FactoryOrchestrator(
            agent.AgentConfig(
                station_registry_file=str(registry_path),
                orchestrator_log_csv_path=str(temp_path / "v2-runs.csv"),
                orchestrator_summary_csv_path=str(temp_path / "v2-summary.csv"),
            )
        )
        try:
            self.assertEqual(
                orchestrator.station_by_conveyor_submodel,
                {
                    "conveyor-state-1": "Station_01",
                    "conveyor-state-2": "Station_02",
                },
            )
            self.assertEqual(
                [robot.robot_id for robot in orchestrator.robots],
                ["Robot_01", "Robot_02"],
            )
            self.assertEqual(
                [robot.station_id for robot in orchestrator.robots],
                ["Station_01", "Station_01"],
            )
        finally:
            await orchestrator.close()

    def test_move_to_home_retains_fixed_home_arguments(self):
        parsed = parse_capability_route(
            route(
                "Station_01",
                sensor="Sensor_ClearRobot",
                operation="ExecuteMoveToHome",
                source=None,
                target=None,
                extra=[{"idShort": "move", "valueType": "xs:boolean", "value": True}],
            )
        )

        inputs = build_operation_inputs(parsed)

        self.assertEqual(inputs, {"move": {"value": True, "valueType": "xs:boolean"}})
        self.assertNotIn("StationId", inputs)
        self.assertNotIn("SourcePosition", inputs)
        self.assertNotIn("TargetPosition", inputs)


if __name__ == "__main__":
    unittest.main()
