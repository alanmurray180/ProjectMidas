from .metal_price import MetalPriceClient
from .cftc import CFTCClient
from .etf import GoldETFClient
from .major_etfs import MajorETFsClient
from .wgc import WGCETFClient

__all__ = [
    "MetalPriceClient",
    "CFTCClient",
    "GoldETFClient",
    "MajorETFsClient",
    "WGCETFClient",
]
