from dataclasses import dataclass, field
from typing import Tuple, List, Optional, Set

BASE_BIT_SIZE = 33  # sender_id(16) + mobile(1) + position(8) + timestamp(4)
BYTE_SIZE = 8

@dataclass(slots=True)
# Represents a beacon message sent by a buoy in the network
class Beacon:
    sender_id: int # 16 bytes
    mobile: bool # 1 byte
    position: Tuple[float, float] # 8 bytes
    # Each entry: (id, last-contact ts, position, hop distance from this beacon's sender);
    # the hop distance is not counted in the on-air size, like the forward-mode hop fields
    neighbors: List[Tuple[int, float, Tuple[float, float], int]] # 16 + 4 + 8 bytes per neighbor
    timestamp: float # 4 bytes
    origin_id: Optional[int] = None  # 16 bytes (only in forward mode)
    hop_limit: int = 0  # 4 bytes (only in forward mode)

    # Simulation-only bookkeeping, not part of the on-air packet (excluded from size_bytes):
    # receiver ids with a still-valid scheduled reception of this beacon; a colliding
    # transmission removes a receiver here so its corrupted copy is dropped on arrival
    scheduled_receivers: Set[int] = field(default_factory=set, compare=False)

    def size_bits(self) -> int:
        base = BASE_BIT_SIZE
        
        # Add size per neighbor: uuid(16) + timestamp(4) + position(8) = 28 bytes
        base += 28 * len(self.neighbors)
        
        # Add multihop fields only if used (forward mode)
        if self.origin_id is not None:
            base += 16  # origin_id
            base += 4   # hop_limit
        
        return base * BYTE_SIZE