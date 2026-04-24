from dataclasses import dataclass
from typing import Tuple, List, Optional
import uuid

@dataclass
# Represents a beacon message sent by a buoy in the network
class Beacon:
    sender_id: uuid.UUID # 16 bytes
    mobile: bool # 1 byte
    position: Tuple[float, float] # 8 bytes
    battery: float # 4 bytes
    neighbors: List[Tuple[uuid.UUID, float, Tuple[float, float]]] # 16 + 4 + 8 bytes per neighbor 
    timestamp: float # 4 bytes
    origin_id: Optional[uuid.UUID] = None  # 16 bytes (only in forwarded mode)
    hop_limit: int = 0  # 4 bytes (only in forwarded mode)

    def size_bytes(self) -> int:
        # Base size: sender_id(16) + mobile(1) + position(8) + battery(4) + timestamp(4) = 37 bytes
        base = 37
        
        # Add size per neighbor: uuid(16) + timestamp(4) + position(8) = 28 bytes
        base += 28 * len(self.neighbors)
        
        # Add multihop fields only if used (forwarded mode)
        if self.origin_id is not None:
            base += 16  # origin_id
            base += 4   # hop_limit
        
        return base

    def size_bits(self) -> int:
        return self.size_bytes() * 8