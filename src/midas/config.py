"""Configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

METALPRICEAPI_KEY: str = os.getenv("METALPRICEAPI_KEY", "")
METALPRICEAPI_BASE: str = "https://api.metalpriceapi.com/v1"

CFTC_BASE: str = "https://www.cftc.gov/files/dea/history"
# Socrata open-data API for the Disaggregated Futures-only report
CFTC_SOCRATA_URL: str = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
CFTC_APP_TOKEN: str = os.getenv("FINANCIAL_ANALYSIS_COT", "")

# SPDR Gold Shares publishes daily holdings
GLD_HOLDINGS_URL: str = "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive.csv"
