"""Context nodes: atomic pieces of context the Producer AI picks for each prompt. Each node is a function registered
with node_registry; importing this module loads every family so they all register. Data fetching lives in
context_service.py; the nodes only present it.

- context_nodes_format: who the hosts are and how they talk (identity, format, tags, guidelines)
- context_nodes_segments: segment instructions and data, studio clock, segment length
- context_nodes_track: the track on air, audio features, queue and playlist view
- context_nodes_listener: the listener, the conversation, the weather
- context_nodes_station: schedule, recent airings, talking points, Radio Mode segments, tool guidance, City Pulse
"""

from services_radio import (context_nodes_format, context_nodes_listener, context_nodes_segments,
                            context_nodes_station, context_nodes_track)

NODE_FAMILIES = (context_nodes_format, context_nodes_segments, context_nodes_track, context_nodes_listener,
                 context_nodes_station)
