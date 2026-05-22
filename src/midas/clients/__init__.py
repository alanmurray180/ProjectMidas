from .metal_price import MetalPriceClient
from .cftc import CFTCClient
from .dxy import DXYClient
from .etf import GoldETFClient
from .major_etfs import MajorETFsClient
from .wgc import WGCETFClient

__all__ = [
    "MetalPriceClient",
    "CFTCClient",
    "DXYClient",
    "GoldETFClient",
    "MajorETFsClient",
    "WGCETFClient",
]
