from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from synapse_delete_room_rest_api.extract_body_json import extract_body_json
from synapse_delete_room_rest_api.is_rate_limited import is_rate_limited
from synapse_delete_room_rest_api.user_has_highest_power_level import (
    user_has_highest_power_level,
)
from synapse_delete_room_rest_api.user_is_room_member import user_is_room_member

if TYPE_CHECKING:
    from synapse_delete_room_rest_api import SynapseDeleteRoomRestAPIConfig

import logging

from synapse.api.errors import (
    AuthError,
    InvalidClientCredentialsError,
    InvalidClientTokenError,
    MissingClientTokenError,
)
from synapse.http import server
from synapse.http.server import respond_with_json
from synapse.http.site import SynapseRequest
from synapse.module_api import ModuleApi
from twisted.internet import defer
from twisted.web.resource import Resource

logger = logging.getLogger("synapse.module.synapse_delete_room_rest_api.delete_room")


class DeleteRoom(Resource):
    isLeaf = True

    def __init__(self, api: ModuleApi, config: SynapseDeleteRoomRestAPIConfig):
        super().__init__()
        self._api = api
        self._config = config
        self._auth = self._api._hs.get_auth()
        self._datastores = self._api._hs.get_datastores()

    def render_POST(self, request: SynapseRequest):
        defer.ensureDeferred(self._async_render_POST(request))
        return server.NOT_DONE_YET

    async def _async_render_POST(self, request: SynapseRequest):
        try:
            requester = await self._auth.get_user_by_req(request)
            requester_id = requester.user.to_string()
            if is_rate_limited(requester_id, self._config):
                respond_with_json(
                    request,
                    429,
                    {"error": "Rate limited"},
                    send_cors=True,
                )
                return
            # Extract body
            body = await extract_body_json(request)
            if not isinstance(body, dict):
                respond_with_json(
                    request,
                    400,
                    {"error": "Invalid JSON in request body"},
                    send_cors=True,
                )
                return

            # Validate body
            room_id = body.get("room_id", None)
            if not isinstance(room_id, str):
                respond_with_json(
                    request,
                    400,
                    {"error": "Missing or invalid room_id"},
                    send_cors=True,
                )
                return

            # Ensure requester is member of the room
            (is_member, room_members_ids) = await user_is_room_member(
                self._api, requester_id, room_id
            )
            if not is_member:
                respond_with_json(
                    request,
                    400,
                    {"error": "Bad request. Not a member of the room"},
                    send_cors=True,
                )
                return

            # Ensure request has highest power level
            if not await user_has_highest_power_level(self._api, requester_id, room_id):
                respond_with_json(
                    request,
                    400,
                    {"error": "Bad request. Not the highest power level"},
                    send_cors=True,
                )
                return

            # Kick all members
            await asyncio.gather(
                *[
                    self._api.update_room_membership(
                        sender=requester_id,
                        target=member_id,
                        room_id=room_id,
                        new_membership="leave",
                    )
                    for member_id in room_members_ids
                ]
            )

            # Leave the room
            await self._api.update_room_membership(
                sender=requester_id,
                target=requester_id,
                room_id=room_id,
                new_membership="leave",
            )

            respond_with_json(
                request,
                200,
                {"message": "Deleted"},
                send_cors=True,
            )
        except (
            MissingClientTokenError,
            InvalidClientTokenError,
            InvalidClientCredentialsError,
            AuthError,
        ) as e:
            logger.error(f"Forbidden: {e}")
            respond_with_json(
                request,
                403,
                {"error": "Forbidden"},
                send_cors=True,
            )

        except Exception as e:
            logger.error(f"Error processing request: {e}")
            respond_with_json(
                request,
                500,
                {"error": "Internal server error"},
                send_cors=True,
            )
