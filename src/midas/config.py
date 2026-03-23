"""Configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

METALPRICEAPI_KEY: str = os.getenv("METALPRICEAPI_KEY", "")
METALPRICEAPI_BASE: str = "https://api.metalpriceapi.com/v1"

CFTC_BASE: str = "https://www.cftc.gov/files/dea/history"
# Disaggregated Futures-only report (includes gold under commodity code 088691)
CFTC_DISAGGREGATED_URL: str = f"{CFTC_BASE}/fut_disagg_txt_2024.zip"

# SPDR Gold Shares publishes daily holdings
GLD_HOLDINGS_URL: str = "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive.csv"
