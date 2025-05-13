from synapse.module_api import ModuleApi

from synapse_delete_room_rest_api.constants import (
    EVENT_TYPE_M_ROOM_MEMBER,
    MEMBERSHIP_CONTENT_KEY,
    MEMBERSHIP_JOIN,
)


async def user_is_room_member(
    api: ModuleApi, user_id: str, room_id: str
) -> tuple[bool, set[str]]:
    room_member_state_events = await api.get_room_state(
        room_id=room_id,
        event_filter=[(EVENT_TYPE_M_ROOM_MEMBER, user_id)],
    )
    room_members = set()
    is_member = False
    for state_event in room_member_state_events.values():
        if (
            state_event.type != EVENT_TYPE_M_ROOM_MEMBER
            or state_event.state_key != user_id
            or state_event.content.get(MEMBERSHIP_CONTENT_KEY) != MEMBERSHIP_JOIN
        ):
            room_members.add(state_event.state_key)
            continue
        is_member = True
        break
    return (is_member, room_members)
