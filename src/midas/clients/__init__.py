from .metal_price import MetalPriceClient
from .cftc import CFTCClient
from .cot_trends import COTTrends
from .dxy import DXYClient
from .etf import GoldETFClient
from .etf_scorecard import GoldETFScorecard
from .fred import FREDClient
from .macro_scorecard import MacroScorecard
from .gold_silver import GoldSilverRatioClient
from .major_etfs import MajorETFsClient
from .swiss_trade import SwissGoldTradeClient
from .vix import VIXClient
from .wgc import WGCETFClient

__all__ = [
    "MetalPriceClient",
    "CFTCClient",
    "COTTrends",
    "DXYClient",
    "GoldETFClient",
    "GoldETFScorecard",
    "FREDClient",
    "MacroScorecard",
    "GoldSilverRatioClient",
    "MajorETFsClient",
    "SwissGoldTradeClient",
    "VIXClient",
    "WGCETFClient",
]
