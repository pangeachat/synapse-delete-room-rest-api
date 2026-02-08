You own the docs. Three sources of truth must agree: **docs**, **code**, and **prior user guidance**. When they don't, resolve it. Update `.github/instructions/` docs when your changes shift conventions. Fix obvious factual errors (paths, class names) without asking. Flag ambiguity when sources contradict.

Synapse module (Python 3.9+). Allows room admins to delete rooms.
- **API**: `POST /_synapse/client/pangea/v1/delete_room` — requires highest power level in the room
