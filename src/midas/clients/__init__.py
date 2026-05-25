from .metal_price import MetalPriceClient
from .cftc import CFTCClient
from .dxy import DXYClient
from .etf import GoldETFClient
from .etf_scorecard import GoldETFScorecard
from .fred import FREDClient
from .gold_silver import GoldSilverRatioClient
from .major_etfs import MajorETFsClient
from .vix import VIXClient
from .wgc import WGCETFClient

__all__ = [
    "MetalPriceClient",
    "CFTCClient",
    "DXYClient",
    "GoldETFClient",
    "GoldETFScorecard",
    "FREDClient",
    "GoldSilverRatioClient",
    "MajorETFsClient",
    "VIXClient",
    "WGCETFClient",
]
