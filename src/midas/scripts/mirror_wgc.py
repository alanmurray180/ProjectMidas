"""Download the latest WGC ETF flows xlsx and write it to the package.

Intended to be run by the scheduled GitHub Actions workflow at
``.github/workflows/mirror-wgc.yml``.  GitHub-hosted runner IPs are
generally not blocked by gold.org's CDN, whereas Render's datacenter
IPs are — so we refresh the file in CI and commit it to the repo,
then the Flask app reads it from disk at runtime.

Exits non-zero on failure so the Action reports it loudly.

Run locally with::

    PYTHONPATH=src python -m midas.scripts.mirror_wgc
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from midas.clients.wgc import (
    _GOLDHUB_LANDING,
    WGCETFClient,
    _new_client,
)

log = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    client = WGCETFClient()
    out_dir = client._cache_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    with _new_client() as http:
        WGCETFClient._warm_session(http)
        xlsx_url, page_url = client._find_latest_xlsx(http)

        if not xlsx_url:
            log.error(
                "Could not locate WGC xlsx URL. The monthly page may not "
                "have been published yet, or gold.org blocked the runner."
            )
            return 1

        log.info("Downloading %s", xlsx_url)
        headers = {
            "Referer": page_url or _GOLDHUB_LANDING,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Accept": "*/*",
        }
        resp = http.get(xlsx_url, headers=headers, timeout=120)
        resp.raise_for_status()

        xlsx_path = out_dir / "latest.xlsx"
        meta_path = out_dir / "metadata.json"

        xlsx_path.write_bytes(resp.content)
        meta_path.write_text(
            json.dumps(
                {
                    "source_url": xlsx_url,
                    "fetched_at": datetime.now(timezone.utc)
                    .isoformat(timespec="seconds"),
                    "size_bytes": len(resp.content),
                },
                indent=2,
            )
            + "\n"
        )

    log.info("Wrote %s (%d bytes)", xlsx_path, xlsx_path.stat().st_size)
    log.info("Wrote %s", meta_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
