from enum import Enum, auto
from typing import Any, Optional

class EventType(Enum):
    SCHEDULER_CHECK = auto()            # Check if buoy should send a beacon
    CHANNEL_SENSE = auto()              # Check if channel is free
    DIFS_COMPLETION = auto()            # DIFS waiting period completes
    BACKOFF_COMPLETION = auto()         # Process backoff slot
    TRANSMISSION_START = auto()         # Buoy starts transmitting
    TRANSMISSION_END = auto()           # Transmission completes
    RECEPTION = auto()                  # Buoy receives a beacon
    NEIGHBOR_CLEANUP = auto()           # Clean up stale neighbor entries
    BUOY_MOVEMENT = auto()              # Update buoy position
    CHANNEL_UPDATE = auto()             # Update channel state
    BUOY_ARRAY_UPDATE = auto()          # Add/remove buoys
    AVG_NEIGHBORS_CALCULATION = auto()  # Periodic calculation of avg neighbors

class Event:
    # Slots: hundreds of thousands of events are created per run
    __slots__ = ('time', 'event_type', 'target_obj', 'data')

    def __init__(self, time: float, event_type: EventType, target_obj: Any, data: Optional[dict] = None):
        self.time: float = time
        self.event_type: EventType = event_type
        self.target_obj: Any = target_obj
        self.data: dict = data or {}