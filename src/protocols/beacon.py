from dataclasses import dataclass, field
from typing import Tuple, List, Optional, Set
import uuid

@dataclass(slots=True)
# Represents a beacon message sent by a buoy in the network
class Beacon:
    sender_id: uuid.UUID # 16 bytes
    mobile: bool # 1 byte
    position: Tuple[float, float] # 8 bytes
    neighbors: List[Tuple[uuid.UUID, float, Tuple[float, float]]] # 16 + 4 + 8 bytes per neighbor
    timestamp: float # 4 bytes
    origin_id: Optional[uuid.UUID] = None  # 16 bytes (only in forwarded mode)
    hop_limit: int = 0  # 4 bytes (only in forwarded mode)

    # Simulation-only bookkeeping, not part of the on-air packet (excluded from size_bytes):
    # receiver ids with a still-valid scheduled reception of this beacon; a colliding
    # transmission removes a receiver here so its corrupted copy is dropped on arrival
    scheduled_receivers: Set[uuid.UUID] = field(default_factory=set, compare=False)

    def size_bytes(self) -> int:
        # Base size: sender_id(16) + mobile(1) + position(8) + timestamp(4) = 33 bytes
        base = 33
        
        # Add size per neighbor: uuid(16) + timestamp(4) + position(8) = 28 bytes
        base += 28 * len(self.neighbors)
        
        # Add multihop fields only if used (forwarded mode)
        if self.origin_id is not None:
            base += 16  # origin_id
            base += 4   # hop_limit
        
        return base

    def size_bits(self) -> int:
        return self.size_bytes() * 8