import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import IO, Literal, Tuple, Union

import aiounittest
import psycopg2
import requests
import testing.postgresql
import yaml
from psycopg2.extensions import parse_dsn

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    filename="synapse.log",
    filemode="w",
)


class TestE2E(aiounittest.AsyncTestCase):
    async def start_test_synapse(
        self,
        db: Literal["sqlite", "postgresql"] = "sqlite",
        postgresql_url: Union[str, None] = None,
    ) -> Tuple[str, str, subprocess.Popen, threading.Thread, threading.Thread]:
        try:
            synapse_dir = tempfile.mkdtemp()
            config_path = os.path.join(synapse_dir, "homeserver.yaml")
            generate_config_cmd = [
                sys.executable,
                "-m",
                "synapse.app.homeserver",
                "--server-name=my.domain.name",
                f"--config-path={config_path}",
                "--report-stats=no",
                "--generate-config",
            ]
            subprocess.check_call(generate_config_cmd)
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            log_config_path = config.get("log_config")
            config["modules"] = [
                {
                    "module": "synapse_delete_room_rest_api.SynapseDeleteRoomRestAPI",
                    "config": {},
                }
            ]
            if db == "sqlite":
                if postgresql_url is not None:
                    self.fail(
                        "PostgreSQL URL must not be defined when using SQLite database"
                    )
                config["database"] = {
                    "name": "sqlite3",
                    "args": {"database": "homeserver.db"},
                }
            elif db == "postgresql":
                if postgresql_url is None:
                    self.fail("PostgreSQL URL is required for PostgreSQL database")
                dsn_params = parse_dsn(postgresql_url)
                config["database"] = {
                    "name": "psycopg2",
                    "args": dsn_params,
                }
            with open(config_path, "w") as f:
                yaml.dump(config, f)
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            with open(log_config_path, "r") as f:
                log_config = yaml.safe_load(f)
            log_config["root"]["handlers"] = ["console"]
            log_config["root"]["level"] = "DEBUG"
            with open(log_config_path, "w") as f:
                yaml.dump(log_config, f)
            run_server_cmd = [
                sys.executable,
                "-m",
                "synapse.app.homeserver",
                "--config-path",
                config_path,
            ]
            server_process = subprocess.Popen(
                run_server_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=synapse_dir,
                text=True,
            )

            def read_output(pipe: Union[IO[str], None]):
                if pipe is None:
                    return
                for line in iter(pipe.readline, ""):
                    logger.debug(line)
                pipe.close()

            stdout_thread = threading.Thread(
                target=read_output, args=(server_process.stdout,)
            )
            stderr_thread = threading.Thread(
                target=read_output, args=(server_process.stderr,)
            )
            stdout_thread.start()
            stderr_thread.start()
            server_url = "http://localhost:8008"
            max_wait_time = 10
            wait_interval = 1
            total_wait_time = 0
            server_ready = False
            while not server_ready and total_wait_time < max_wait_time:
                try:
                    response = requests.get(server_url)
                    if response.status_code == 200:
                        server_ready = True
                        break
                except requests.exceptions.ConnectionError:
                    pass
                finally:
                    await asyncio.sleep(wait_interval)
                    total_wait_time += wait_interval
            if not server_ready:
                self.fail("Synapse server did not start successfully")
            return (
                synapse_dir,
                config_path,
                server_process,
                stdout_thread,
                stderr_thread,
            )
        except Exception as e:
            server_process.terminate()
            server_process.wait()
            stdout_thread.join()
            stderr_thread.join()
            shutil.rmtree(synapse_dir)
            raise e

    async def start_test_postgres(self):
        postgresql = None
        try:
            postgresql = testing.postgresql.Postgresql()
            postgres_url = postgresql.url()
            max_waiting_time = 10
            wait_interval = 1
            total_wait_time = 0
            postgres_is_up = False
            while total_wait_time < max_waiting_time and not postgres_is_up:
                try:
                    conn = psycopg2.connect(postgres_url)
                    conn.close()
                    postgres_is_up = True
                    break
                except psycopg2.OperationalError:
                    await asyncio.sleep(wait_interval)
                    total_wait_time += wait_interval
            if not postgres_is_up:
                postgresql.stop()
                self.fail("Postgres did not start successfully")
            dbname = "testdb"
            conn = psycopg2.connect(postgres_url)
            conn.autocommit = True
            cursor = conn.cursor()
            cursor.execute(
                f"""
                CREATE DATABASE {dbname}
                WITH TEMPLATE template0
                LC_COLLATE 'C'
                LC_CTYPE 'C';
            """
            )
            cursor.close()
            conn.close()
            dsn_params = parse_dsn(postgres_url)
            dsn_params["dbname"] = dbname
            postgres_url_testdb = psycopg2.extensions.make_dsn(**dsn_params)
            return postgresql, postgres_url_testdb
        except Exception as e:
            if postgresql is not None:
                postgresql.stop()
            raise e

    async def register_user(
        self, config_path: str, dir: str, user: str, password: str, admin: bool
    ):
        register_user_cmd = [
            "register_new_matrix_user",
            f"-c={config_path}",
            f"--user={user}",
            f"--password={password}",
        ]
        if admin:
            register_user_cmd.append("--admin")
        else:
            register_user_cmd.append("--no-admin")
        subprocess.check_call(register_user_cmd, cwd=dir)

    async def login_user(self, user: str, password: str) -> str:
        login_url = "http://localhost:8008/_matrix/client/v3/login"
        login_data = {
            "type": "m.login.password",
            "user": user,
            "password": password,
        }
        response = requests.post(login_url, json=login_data)
        self.assertEqual(response.status_code, 200)
        return response.json()["access_token"]

    async def create_private_room(self, access_token: str) -> str:
        headers = {"Authorization": f"Bearer {access_token}"}
        create_room_url = "http://localhost:8008/_matrix/client/v3/createRoom"
        create_room_data = {"visibility": "private", "preset": "private_chat"}
        response = requests.post(
            create_room_url,
            json=create_room_data,
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["room_id"]

    async def test_delete_room_sqlite(self):
        await self._test_delete_room(db="sqlite")

    async def test_delete_room_postgres(self):
        await self._test_delete_room(db="postgresql")

    async def _test_delete_room(self, db: Literal["sqlite", "postgresql"]):
        postgres = None
        postgres_url = None
        synapse_dir = None
        server_process = None
        stdout_thread = None
        stderr_thread = None
        try:
            if db == "postgresql":
                postgres, postgres_url = await self.start_test_postgres()
            (
                synapse_dir,
                config_path,
                server_process,
                stdout_thread,
                stderr_thread,
            ) = await self.start_test_synapse(db=db, postgresql_url=postgres_url)

            # Register users (all as non-server-admins)
            users = [
                {"user": "roomadmin", "password": "pw1"},
                {"user": "user2", "password": "pw2"},
                {"user": "user3", "password": "pw3"},
                {"user": "anotheradmin", "password": "pw4"},
            ]
            for register_user in users:
                await self.register_user(
                    config_path=config_path,
                    dir=synapse_dir,
                    user=register_user["user"],
                    password=register_user["password"],
                    admin=False,
                )
            # Login users
            tokens = {}
            for token_user in users:
                tokens[token_user["user"]] = await self.login_user(
                    token_user["user"], token_user["password"]
                )
            # "roomadmin" creates private room
            admin_token = tokens["roomadmin"]
            room_id = await self.create_private_room(admin_token)
            # "roomadmin" invites others
            for invite_user in ["user2", "user3"]:
                invited = await self.invite_user_to_room(
                    room_id, f"@{invite_user}:my.domain.name", admin_token
                )
                self.assertTrue(invited)
            # Others accept invite
            for invite_user in ["user2", "user3"]:
                accepted = await self.accept_room_invitation(
                    room_id, f"@{invite_user}:my.domain.name", tokens[invite_user]
                )
                self.assertTrue(accepted)

            # Verify all users are in the room
            for joined_user in ["roomadmin", "user2", "user3"]:
                member_url = f"http://localhost:8008/_matrix/client/v3/rooms/{room_id}/state/m.room.member/@{joined_user}:my.domain.name"
                resp = requests.get(
                    member_url,
                    headers={"Authorization": f"Bearer {tokens[joined_user]}"},
                )
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json().get("membership"), "join")

            # Non-room-admin tries to delete (should fail)
            delete_url = "http://localhost:8008/_synapse/client/pangea/v1/delete_room"
            response = requests.post(
                delete_url,
                json={"room_id": room_id},
                headers={"Authorization": f"Bearer {tokens['user2']}"},
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("highest power level", response.json().get("error", ""))
            # Room creator deletes (should succeed)
            response = requests.post(
                delete_url,
                json={"room_id": room_id},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["message"], "Deleted")
            # Assert other users are no longer in the room
            for remove_user in ["user2", "user3"]:
                resp = requests.get(
                    "http://localhost:8008/_matrix/client/v3/joined_rooms",
                    headers={"Authorization": f"Bearer {tokens[remove_user]}"},
                )
                self.assertEqual(resp.status_code, 200)
                joined_rooms = resp.json().get("joined_rooms", [])
                self.assertEqual(len(joined_rooms), 0)

            # "roomadmin" creates another private room and invite anotheradmin
            room_id = await self.create_private_room(admin_token)
            for admin in ["anotheradmin"]:
                invited = await self.invite_user_to_room(
                    room_id, f"@{admin}:my.domain.name", admin_token
                )
                self.assertTrue(invited)

            # "anotheradmin" accept invite
            for invited_admin in ["anotheradmin"]:
                accepted = await self.accept_room_invitation(
                    room_id, f"@{invited_admin}:my.domain.name", tokens[invited_admin]
                )
                self.assertTrue(accepted)

            # Update anotheradmin to be room admin
            for updated_admin in ["anotheradmin"]:
                await self.set_member_power_level(
                    room_id, f"@{updated_admin}:my.domain.name", 100, admin_token
                )

            # Verify all users are in the room and is admin
            for admin_member in ["roomadmin", "anotheradmin"]:
                member_url = f"http://localhost:8008/_matrix/client/v3/rooms/{room_id}/state/m.room.member/@{admin_member}:my.domain.name"
                resp = requests.get(
                    member_url,
                    headers={"Authorization": f"Bearer {tokens[admin_member]}"},
                )
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json().get("membership"), "join")

                power_level = await self.get_member_power_level(
                    room_id, f"@{admin_member}:my.domain.name", admin_token
                )
                self.assertEqual(power_level, 100)

            # roomadmin deletes (should succeed)
            response = requests.post(
                delete_url,
                json={"room_id": room_id},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["message"], "Deleted")
            # Assert other users are no longer in the room
            for ex_admin in ["anotheradmin"]:
                resp = requests.get(
                    "http://localhost:8008/_matrix/client/v3/joined_rooms",
                    headers={"Authorization": f"Bearer {tokens[ex_admin]}"},
                )
                self.assertEqual(resp.status_code, 200)
                joined_rooms = resp.json().get("joined_rooms", [])
                self.assertEqual(len(joined_rooms), 0)

        finally:
            if postgres is not None:
                postgres.stop()
            if server_process is not None:
                server_process.terminate()
                server_process.wait()
            if stdout_thread is not None:
                stdout_thread.join()
            if stderr_thread is not None:
                stderr_thread.join()
            if synapse_dir is not None:
                shutil.rmtree(synapse_dir)

    async def invite_user_to_room(
        self, room_id: str, user_id: str, access_token: str
    ) -> bool:
        invite_url = f"http://localhost:8008/_matrix/client/v3/rooms/{room_id}/invite"
        invite_data = {"user_id": user_id}
        response = requests.post(
            invite_url,
            json=invite_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return response.status_code == 200

    async def accept_room_invitation(
        self, room_id: str, user_id: str, access_token: str
    ) -> bool:
        join_url = f"http://localhost:8008/_matrix/client/v3/join/{room_id}"
        response = requests.post(
            join_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return response.status_code == 200

    async def set_member_power_level(
        self, room_id: str, user_id: str, power_level: int, access_token: str
    ):
        base_url = "http://localhost:8008/_matrix/client/v3"
        headers = {"Authorization": f"Bearer {access_token}"}

        # 1. Fetch current power levels
        power_levels_url = f"{base_url}/rooms/{room_id}/state/m.room.power_levels"
        get_response = requests.get(power_levels_url, headers=headers)
        if get_response.status_code != 200:
            print(
                f"Error fetching power levels: {get_response.status_code} {get_response.text}"
            )
            return False

        power_levels = get_response.json()

        # 2. Update the user's power level
        if "users" not in power_levels:
            power_levels["users"] = {}
        power_levels["users"][user_id] = power_level

        # 3. Send the updated state
        put_response = requests.put(
            power_levels_url, headers=headers, json=power_levels
        )
        if put_response.status_code not in (200, 201):
            print(
                f"Error updating power levels: {put_response.status_code} {put_response.text}"
            )
            return False

        return True

    async def get_member_power_level(
        self, room_id: str, user_id: str, access_token: str
    ):
        base_url = "http://localhost:8008/_matrix/client/v3"
        headers = {"Authorization": f"Bearer {access_token}"}

        # 1. Fetch current power levels
        power_levels_url = f"{base_url}/rooms/{room_id}/state/m.room.power_levels"
        get_response = requests.get(power_levels_url, headers=headers)
        if get_response.status_code != 200:
            print(
                f"Error fetching power levels: {get_response.status_code} {get_response.text}"
            )
            return False

        power_levels = get_response.json()

        user_permissions = power_levels.get("users", {})

        users_default = power_levels.get("user_defaults", 0)

        return user_permissions.get(user_id, users_default)

    async def create_space(self, access_token: str, name: str = "Test Space") -> str:
        """Create a new space and return its room_id."""
        headers = {"Authorization": f"Bearer {access_token}"}
        create_space_url = "http://localhost:8008/_matrix/client/v3/createRoom"
        create_space_data = {
            "visibility": "private",
            "preset": "private_chat",
            "creation_content": {"type": "m.space"},
            "name": name,
        }
        response = requests.post(
            create_space_url,
            json=create_space_data,
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["room_id"]

    async def add_child_to_space(
        self, space_id: str, child_room_id: str, access_token: str
    ) -> bool:
        """Add a child room to a space."""
        headers = {"Authorization": f"Bearer {access_token}"}
        space_child_url = f"http://localhost:8008/_matrix/client/v3/rooms/{space_id}/state/m.space.child/{child_room_id}"
        space_child_data = {
            "via": ["my.domain.name"],
            "order": "01",
        }
        response = requests.put(
            space_child_url,
            json=space_child_data,
            headers=headers,
        )
        return response.status_code in (200, 201)

    async def add_parent_to_room(
        self, room_id: str, parent_space_id: str, access_token: str
    ) -> bool:
        """Add a parent space to a room."""
        headers = {"Authorization": f"Bearer {access_token}"}
        space_parent_url = f"http://localhost:8008/_matrix/client/v3/rooms/{room_id}/state/m.space.parent/{parent_space_id}"
        space_parent_data = {
            "via": ["my.domain.name"],
            "canonical": True,
        }
        response = requests.put(
            space_parent_url,
            json=space_parent_data,
            headers=headers,
        )
        return response.status_code in (200, 201)

    async def get_space_children(self, space_id: str, access_token: str) -> list:
        """Get the children of a space by looking at m.space.child state events with non-empty content."""
        headers = {"Authorization": f"Bearer {access_token}"}
        # Get all state events from the space
        state_url = f"http://localhost:8008/_matrix/client/v3/rooms/{space_id}/state"
        response = requests.get(state_url, headers=headers)
        if response.status_code != 200:
            return []

        state_events = response.json()
        space_child_events = []
        for event in state_events:
            if event.get("type") == "m.space.child" and event.get("content"):
                # Only include events with non-empty content (active relationships)
                space_child_events.append(event)
        return space_child_events

    async def get_room_space_parents(self, room_id: str, access_token: str) -> dict:
        """Get the space parent events for a room."""
        headers = {"Authorization": f"Bearer {access_token}"}
        # Get all m.space.parent events from the room state
        state_url = f"http://localhost:8008/_matrix/client/v3/rooms/{room_id}/state"
        response = requests.get(state_url, headers=headers)
        if response.status_code != 200:
            return {}

        state_events = response.json()
        space_parent_events = {}
        for event in state_events:
            if event.get("type") == "m.space.parent":
                space_parent_events[event["state_key"]] = event
        return space_parent_events

    async def test_delete_room_with_space_relationships_sqlite(self):
        await self._test_delete_room_with_space_relationships(db="sqlite")

    async def test_delete_room_with_space_relationships_postgres(self):
        await self._test_delete_room_with_space_relationships(db="postgresql")

    async def _test_delete_room_with_space_relationships(
        self, db: Literal["sqlite", "postgresql"]
    ):
        """Test that space relationships are properly cleaned up when a room is deleted."""
        postgres = None
        postgres_url = None
        synapse_dir = None
        server_process = None
        stdout_thread = None
        stderr_thread = None
        try:
            if db == "postgresql":
                postgres, postgres_url = await self.start_test_postgres()
            (
                synapse_dir,
                config_path,
                server_process,
                stdout_thread,
                stderr_thread,
            ) = await self.start_test_synapse(db=db, postgresql_url=postgres_url)

            # Register users
            users = [
                {"user": "spaceadmin", "password": "pw1"},
                {"user": "roomcreator", "password": "pw2"},
            ]
            for register_user in users:
                await self.register_user(
                    config_path=config_path,
                    dir=synapse_dir,
                    user=register_user["user"],
                    password=register_user["password"],
                    admin=False,
                )

            # Login users
            tokens = {}
            for token_user in users:
                tokens[token_user["user"]] = await self.login_user(
                    token_user["user"], token_user["password"]
                )

            space_admin_token = tokens["spaceadmin"]
            room_creator_token = tokens["roomcreator"]

            # Create a space
            space_id = await self.create_space(space_admin_token, "Test Space")

            # Create a regular room
            room_id = await self.create_private_room(room_creator_token)

            # Invite room creator to space and vice versa to allow space relationship setup
            await self.invite_user_to_room(
                space_id, "@roomcreator:my.domain.name", space_admin_token
            )
            await self.accept_room_invitation(
                space_id, "@roomcreator:my.domain.name", room_creator_token
            )

            # Give room creator admin powers in the space so they can modify space relationships
            await self.set_member_power_level(
                space_id, "@roomcreator:my.domain.name", 100, space_admin_token
            )

            await self.invite_user_to_room(
                room_id, "@spaceadmin:my.domain.name", room_creator_token
            )
            await self.accept_room_invitation(
                room_id, "@spaceadmin:my.domain.name", space_admin_token
            )

            # Set up space relationships
            # Add the room as a child of the space
            child_added = await self.add_child_to_space(
                space_id, room_id, space_admin_token
            )
            self.assertTrue(child_added, "Failed to add child to space")

            # Add the space as a parent of the room
            parent_added = await self.add_parent_to_room(
                room_id, space_id, room_creator_token
            )
            self.assertTrue(parent_added, "Failed to add parent to room")

            # Verify relationships exist before deletion
            space_children = await self.get_space_children(space_id, space_admin_token)
            child_room_ids = [child["state_key"] for child in space_children]
            self.assertIn(
                room_id, child_room_ids, "Room should be a child of the space"
            )

            room_parents = await self.get_room_space_parents(
                room_id, room_creator_token
            )
            self.assertIn(
                space_id, room_parents, "Space should be a parent of the room"
            )

            # Delete the room using our API
            delete_url = "http://localhost:8008/_synapse/client/pangea/v1/delete_room"
            response = requests.post(
                delete_url,
                json={"room_id": room_id},
                headers={"Authorization": f"Bearer {room_creator_token}"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["message"], "Deleted")

            # Wait a moment for the cleanup to complete
            await asyncio.sleep(1)

            # Verify space relationships are cleaned up
            space_children_after = await self.get_space_children(
                space_id, space_admin_token
            )
            child_room_ids_after = [
                child["state_key"] for child in space_children_after
            ]

            self.assertNotIn(
                room_id,
                child_room_ids_after,
                "Room should no longer be a child of the space after deletion",
            )

        finally:
            if postgres is not None:
                postgres.stop()
            if server_process is not None:
                server_process.terminate()
                server_process.wait()
            if stdout_thread is not None:
                stdout_thread.join()
            if stderr_thread is not None:
                stderr_thread.join()
            if synapse_dir is not None:
                shutil.rmtree(synapse_dir)
