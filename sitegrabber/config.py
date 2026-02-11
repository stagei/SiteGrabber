"""Configuration dataclass for SiteGrabber."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CrawlConfig:
    """Configuration for a crawl session."""

    input_address: str
    output_folder: str
    limitation_type: Optional[str] = None
    limitation_text: Optional[str] = None
    recursive: bool = True
    delay: float = 0.5
    max_pages: int = 0  # 0 = unlimited
    timeout: int = 30
    resume: bool = False
    verbose: bool = False
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36 SiteGrabber/1.0"
    )
