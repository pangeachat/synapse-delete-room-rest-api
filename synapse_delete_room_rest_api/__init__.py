from typing import Any, Dict

import attr
from synapse.module_api import ModuleApi

from synapse_delete_room_rest_api.delete_room import DeleteRoom


@attr.s(auto_attribs=True, frozen=True)
class SynapseDeleteRoomRestAPIConfig:
    delete_room_requests_per_burst: int = 10
    delete_room_burst_duration_seconds: int = 60


class SynapseDeleteRoomRestAPI:
    def __init__(self, config: SynapseDeleteRoomRestAPIConfig, api: ModuleApi):
        self._api = api
        self._config = config
        self.delete_room_resource = DeleteRoom(api, config)
        self._api.register_web_resource(
            path="/_synapse/client/pangea/v1/delete_room",
            resource=self.delete_room_resource,
        )

    @staticmethod
    def parse_config(config: Dict[str, Any]) -> SynapseDeleteRoomRestAPIConfig:
        # Parse the module's configuration here.
        # If there is an issue with the configuration, raise a
        # synapse.module_api.errors.ConfigError.
        #
        # Example:
        #
        #     some_option = config.get("some_option")
        #     if some_option is None:
        #          raise ConfigError("Missing option 'some_option'")
        #      if not isinstance(some_option, str):
        #          raise ConfigError("Config option 'some_option' must be a string")
        #
        return SynapseDeleteRoomRestAPIConfig()
