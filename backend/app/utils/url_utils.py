import os
from urllib.parse import urlparse


def get_filename_from_url(url: str, default_title: str) -> str:
    """Extract filename from URL path or return default title if extraction fails.

    Args:
        url: The URL to extract the filename from
        default_title: Fallback title if extraction fails

    Returns:
        Extracted filename or default title
    """
    try:
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path)
        if filename and filename != '/':
            return filename
    except Exception:
        pass  # Ignore URL parsing errors, will use fallback

    return default_title
