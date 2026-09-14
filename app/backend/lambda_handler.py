"""
AWS Lambda handler for unified frontend and backend with Nginx reverse proxy
This handler simulates Nginx routing logic within Lambda
"""
import asyncio
import base64
import hashlib
import html as html_mod
import io
import json
import logging
import os
import re
import sys
import traceback
from contextlib import contextmanager
from typing import Any, Dict
from urllib.parse import unquote

from mangum import Mangum

# This file is moved to /var/task during image assembly while the package stays
# under /var/task/backend. In the source template both remain side by side.
_HANDLER_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
_PACKAGED_BACKEND_DIRECTORY = os.path.join(_HANDLER_DIRECTORY, "backend")
if os.path.isdir(_PACKAGED_BACKEND_DIRECTORY) and _PACKAGED_BACKEND_DIRECTORY not in sys.path:
    sys.path.append(_PACKAGED_BACKEND_DIRECTORY)


@contextmanager
def measure_phase(_name):
    yield


def timed_phase(_name):
    return lambda operation: operation


def summarize_lambda_handler(handler):
    return handler


try:
    from core.telemetry import (  # noqa: E402
        measure_phase,
        summarize_lambda_handler,
        timed_phase,
    )
except Exception:
    # Request diagnostics are optional and must never block Lambda startup.
    pass

# Configure logging
logger = logging.getLogger()
if logger.hasHandlers():
    logger.handlers.clear()
fmt = logging.Formatter("%(asctime)s - %(module)s.%(funcName)s - %(levelname)s - %(pathname)s:%(lineno)d - %(message)s")
h = logging.StreamHandler()
h.setFormatter(fmt)
logger.setLevel(getattr(logging, os.environ.get("LOG_LEVEL", "DEBUG").upper(), logging.DEBUG))
logger.addHandler(h)

# Global variables for app instances
backend_app = None
mangum_handler = None
services_initialized = False

# Dynamic route registry - initialized on first request
dynamic_routes_initialized = False
seo_paths = set()

# SEO domain placeholder - will be replaced with actual request domain at runtime
SEO_DOMAIN_PLACEHOLDER = "https://atoms.template.com"


def _run_async_blocking(coro):
    """Run an async initializer from Lambda's synchronous handler path."""
    import warnings

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="There is no current event loop", category=DeprecationWarning)
            loop = asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.run(coro)

    if loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()

    return loop.run_until_complete(coro)


def format_traceback() -> str:
    """Format traceback with newlines replaced by '\\n' string literal"""
    return traceback.format_exc().replace(chr(10), "\\n")


def initialize_dynamic_routes():
    """Initialize dynamic routes by scanning frontend dist directory"""
    global dynamic_routes_initialized, seo_paths
    
    if dynamic_routes_initialized:
        return
    
    dist_path = "/var/task/frontend/dist"

    try:
        if os.path.exists(dist_path):
            for root, dirs, files in os.walk(dist_path):
                if "index.html" in files:
                    rel_path = os.path.relpath(root, dist_path)

                    # Skip root index.html (for SPA)
                    if rel_path == ".":
                        continue

                    url_path = "/" + rel_path.replace(os.sep, "/")

                    # Only register SEO paths
                    if url_path == "/blog" or url_path.startswith("/blog/"):
                        seo_paths.add(url_path)
                        logger.info(f"Registered SEO route: {url_path}")

        dynamic_routes_initialized = True
        logger.info(f"Dynamic routes initialized: seo_paths={len(seo_paths)}")

    except Exception as e:
        logger.error(f"Failed to initialize dynamic routes: {e}\n{format_traceback()}")
        dynamic_routes_initialized = True


@timed_phase("init.services")
async def initialize_services_once():
    """Initialize all services once for the Lambda function (equivalent to FastAPI lifespan startup)"""
    global services_initialized

    if not services_initialized:
        try:
            # Import all initialization functions (same as in main.py lifespan)
            # Add backend to sys.path for imports
            if "/var/task/backend" not in sys.path:
                sys.path.append("/var/task/backend")

            from core.environment import get_env_bool

            if not get_env_bool("MGX_IGNORE_MODULE_INIT", default=True):
                with measure_phase("init.service_imports"):
                    # Injection tooling derives indentation here; pass keeps zero-module builds valid.
                    # MODULE_IMPORTS_START
                    pass
                    from services.database import initialize_database
                    # MODULE_IMPORTS_END

                # MODULE_STARTUP_START
                await initialize_database()
                # MODULE_STARTUP_END
                pass

            services_initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize services: {e}\n{format_traceback()}")
            raise


@timed_phase("init.app")
def get_backend_app():
    """Get or create the FastAPI backend app"""
    global backend_app

    if backend_app is None:
        try:
            # Import the FastAPI app
            if "/var/task/backend" not in sys.path:
                sys.path.append("/var/task/backend")
            # Check if main.py exists
            main_py_path = "/var/task/backend/main.py"
            if not os.path.exists(main_py_path):
                logger.error("/var/task/backend/main.py not found")

            from main import app as backend_app
        except Exception as e:
            logger.error(f"Failed to import backend app: {e}\n{format_traceback()}")
            raise

    return backend_app


def get_mangum_handler_sync():
    """Get or create the Mangum handler (synchronous version)"""
    global mangum_handler

    if mangum_handler is None:
        try:
            backend_app = get_backend_app()
            # Configure Mangum for API Gateway v2
            mangum_handler = Mangum(backend_app, lifespan="off")
        except Exception as e:
            logger.error(f"Failed to create Mangum handler: {e}\n{format_traceback()}")
            raise

    return mangum_handler


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler function that simulates Nginx routing
    """
    try:
        # Extract request information from the event
        # Support both API Gateway v1 and v2 event formats
        if "version" in event and event["version"] == "2.0":
            # API Gateway v2 format
            path = event.get("rawPath", "/")
            headers = event.get("headers", {})
            query_params = event.get("queryStringParameters", {})
            # API Gateway v2 uses different header format
            if headers:
                # Convert v2 headers to v1 format for Mangum
                headers = {k.lower(): v for k, v in headers.items()}
        elif "httpMethod" in event:
            # API Gateway v1 format
            path = event.get("path", "/")
            headers = event.get("headers", {})
            query_params = event.get("queryStringParameters", {})
        else:
            # Fallback for empty or malformed events
            path = "/"
            headers = {}
            query_params = {}

        # Decode URL-encoded path to handle non-ASCII characters (UTF-8 decode)
        # This ensures non-English characters in URLs are properly decoded
        try:
            path = unquote(path, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to decode path '{path}': {e}, using original path")
            # If decoding fails, use original path

        # Normalize path - ensure it starts with /
        if not path.startswith("/"):
            path = "/" + path

        # Extract real request domain for SEO content replacement
        # Priority: mgx-external-domain > x-forwarded-host > host
        proto = headers.get("x-forwarded-proto", "https")
        host = headers.get("mgx-external-domain") or headers.get("x-forwarded-host") or headers.get("host", "")
        request_domain = f"{proto}://{host}" if host else ""

        # Update the event with decoded and normalized path for downstream handlers
        if "version" in event and event["version"] == "2.0":
            event["rawPath"] = path
        elif "httpMethod" in event:
            event["path"] = path

        # Handle /api/config endpoint
        if path == "/api/config":
            return handle_config_request(headers, query_params)

        # Route API requests to backend
        elif path.startswith("/api/v1/"):
            result = handle_backend_request_sync(event, context)
            return result

        # Handle health check
        elif path == "/health":
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
                "body": json.dumps({"status": "healthy"}),
            }

        # Block database routes
        elif path.startswith("/database"):
            return {
                "statusCode": 404,
                "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
                "body": json.dumps({"error": "Not found"}),
            }
        elif path.endswith(
            (
                ".js", ".css", ".html", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
                ".woff", ".woff2", ".ttf", ".eot",
                ".mp4", ".webm", ".mov", ".m4v", ".ogg", ".ogv", ".mp3", ".wav", ".m4a",
            )
        ):
            # Serve static files (includes .html for Google Search Console verification, etc.)
            return serve_static_file(path, headers)
        
        elif path == "/sitemap.xml":
            return serve_sitemap(request_domain)
        
        elif path == "/robots.txt":
            return serve_robots(request_domain)

        else:
            # Dynamically registered routes: SEO HTML pages (only if exact path is registered).
            # Frontend route scanning is kept out of the API path to avoid first API request latency.
            initialize_dynamic_routes()
            if path.rstrip("/") in seo_paths:
                return serve_seo_html(path, request_domain)

            # Route to frontend (SPA) - ALL other paths go to frontend
            result = serve_frontend()
            return result

    except Exception as e:
        error_info = f"{e}\n{format_traceback()}" if os.getenv("ENVIRONMENT", "prod").lower() == "dev" else str(e)
        logger.error(f"Lambda handler error: {error_info}")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": error_info, "message": "Internal server error"}),
        }


def handle_backend_request_sync(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Handle backend API requests using Mangum (synchronous wrapper)"""
    needs_initialization = not services_initialized or mangum_handler is None
    if needs_initialization:
        with measure_phase("initialization"):
            if not services_initialized:
                _run_async_blocking(initialize_services_once())
            mangum = get_mangum_handler_sync()
    else:
        mangum = get_mangum_handler_sync()

    # Call Mangum handler
    result = mangum(event, context)
    return result


# ---------------------------------------------------------------------------
# Overview injection – read OVERVIEW_* env vars and rewrite data-mgx-overview
# tagged HTML elements so the live Lambda response always reflects the latest
# overview metadata, even after the container image was built.
# ---------------------------------------------------------------------------

_OVERVIEW_MARKER = "data-mgx-overview"

_OVERVIEW_TAG_MAP = [
    # (marker_key, build_replacement)
    ("title", lambda v: f'<title {_OVERVIEW_MARKER}="title">{v}</title>'),
    ("description", lambda v: f'<meta {_OVERVIEW_MARKER}="description" name="description" content="{v}" />'),
    ("og:title", lambda v: f'<meta {_OVERVIEW_MARKER}="og:title" property="og:title" content="{v}" />'),
    ("og:description", lambda v: f'<meta {_OVERVIEW_MARKER}="og:description" property="og:description" content="{v}" />'),
    ("og:image", lambda v: f'<meta {_OVERVIEW_MARKER}="og:image" property="og:image" content="{v}" />'),
    ("twitter:title", lambda v: f'<meta {_OVERVIEW_MARKER}="twitter:title" name="twitter:title" content="{v}" />'),
    ("twitter:description", lambda v: f'<meta {_OVERVIEW_MARKER}="twitter:description" name="twitter:description" content="{v}" />'),
    ("twitter:image", lambda v: f'<meta {_OVERVIEW_MARKER}="twitter:image" name="twitter:image" content="{v}" />'),
    ("icon", lambda v: f'<link {_OVERVIEW_MARKER}="icon" rel="icon" href="{v}" />'),
    ("dark-icon", lambda v: f'<link {_OVERVIEW_MARKER}="dark-icon" rel="icon" media="(prefers-color-scheme: dark)" href="{v}" />'),
]

_OVERVIEW_KEY_TO_VALUE_SOURCE = {
    "title": "title",
    "description": "description",
    "og:title": "title",
    "og:description": "description",
    "og:image": "logo_url",
    "twitter:title": "title",
    "twitter:description": "description",
    "twitter:image": "logo_url",
    "icon": "logo_url",
    "dark-icon": "dark_logo_url",
}


def _inject_overview_env(html_content: str) -> str:
    """Inject overview metadata from OVERVIEW_* environment variables into HTML.

    Uses the same ``data-mgx-overview`` marker convention as the backend
    ``rewrite_overview_in_html`` utility so the result is idempotent.
    """
    title = os.environ.get("OVERVIEW_TITLE", "")
    description = os.environ.get("OVERVIEW_DESCRIPTION", "")
    logo_url = os.environ.get("OVERVIEW_LOGO_URL", "")
    dark_logo_url = os.environ.get("OVERVIEW_DARK_LOGO_URL", "")

    if not any((title, description, logo_url, dark_logo_url)):
        return html_content

    values = {
        "title": html_mod.escape(title, quote=True) if title else "",
        "description": html_mod.escape(description, quote=True) if description else "",
        "logo_url": html_mod.escape(logo_url, quote=True) if logo_url else "",
        "dark_logo_url": html_mod.escape(dark_logo_url, quote=True) if dark_logo_url else "",
    }

    for key, build_fn in _OVERVIEW_TAG_MAP:
        source = _OVERVIEW_KEY_TO_VALUE_SOURCE[key]
        val = values.get(source, "")
        if not val:
            continue
        pattern = re.compile(
            rf'<([a-zA-Z]+)\s[^>]*{_OVERVIEW_MARKER}="{re.escape(key)}"[^>]*(?:/>|>[^<]*</\1>|>)',
            re.IGNORECASE | re.DOTALL,
        )
        m = pattern.search(html_content)
        if m:
            html_content = html_content[:m.start()] + build_fn(val) + html_content[m.end():]
        else:
            # P3: inject before </head> if marker tag not found
            head_m = re.search(r"</head>", html_content, re.IGNORECASE)
            if head_m:
                inject = "  " + build_fn(val) + "\n"
                html_content = html_content[:head_m.start()] + inject + html_content[head_m.start():]

    # Step 3: dark-icon post-processing
    # When both light and dark icons exist, light icon needs media query
    # so it doesn't unconditionally override dark icon.
    dark_icon_val = values.get("dark_logo_url", "")
    light_icon_val = values.get("logo_url", "")
    light_icon_re = re.compile(
        rf'<link\s[^>]*{_OVERVIEW_MARKER}="icon"[^>]*/?\s*>',
        re.IGNORECASE,
    )
    if dark_icon_val and light_icon_val:
        m = light_icon_re.search(html_content)
        if m and 'media="(prefers-color-scheme: light)"' not in m.group(0):
            updated = m.group(0).replace('rel="icon"', 'rel="icon" media="(prefers-color-scheme: light)"')
            html_content = html_content[:m.start()] + updated + html_content[m.end():]
    elif not dark_icon_val:
        # Remove dark-icon tag if dark_logo_url is cleared
        dark_re = re.compile(
            rf'<([a-zA-Z]+)\s[^>]*{_OVERVIEW_MARKER}="dark-icon"[^>]*(?:/>|>[^<]*</\1>|>)',
            re.IGNORECASE | re.DOTALL,
        )
        html_content = dark_re.sub("", html_content)
        # Remove media attribute from light icon
        m = light_icon_re.search(html_content)
        if m and 'media="(prefers-color-scheme: light)"' in m.group(0):
            updated = m.group(0).replace(' media="(prefers-color-scheme: light)"', '')
            html_content = html_content[:m.start()] + updated + html_content[m.end():]

    return html_content


def serve_frontend() -> Dict[str, Any]:
    """Serve the frontend HTML"""
    # Try to read the built frontend HTML
    html_path = "/var/task/frontend/dist/index.html"
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        print(f"[OVERVIEW_DEBUG] handler_version=2025-05-29-v2, "
              f"OVERVIEW_TITLE={os.environ.get('OVERVIEW_TITLE', '<unset>')}, "
              f"OVERVIEW_DESCRIPTION={os.environ.get('OVERVIEW_DESCRIPTION', '<unset>')}, "
              f"OVERVIEW_LOGO_URL={os.environ.get('OVERVIEW_LOGO_URL', '<unset>')}, "
              f"html_has_marker={'data-mgx-overview' in html_content}")
        html_content = _inject_overview_env(html_content)
        response = {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html", "Access-Control-Allow-Origin": "*"},
            "body": html_content,
        }
        return response
    else:
        # Fallback to a simple HTML response
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Unknown App</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                code {
                    background-color: #f4f4f4;
                    border: 1px solid #ddd;
                    border-radius: 3px;
                    padding: 2px 4px;
                    font-family: 'Courier New', monospace;
                    font-size: 0.9em;
                    color: #e33;
                }
            </style>
        </head>
        <body>
            <h1>Unknown Application</h1>
            <p>✅ Service has been successfully deployed and is running on AWS Lambda</p>
            <p>⚠️ The homepage is missing. Please verify that <code>app/frontend/index.html</code> exists and
             that no other <code>index.html</code> files are interfering with the build output
              at <code>dist/index.html</code>.</p>
            <script>
                console.error('INDEX_HTML_PATH_ERROR: Service has been successfully deployed and is running on'
                + ' AWS Lambda, but the homepage is missing. Please verify that app/frontend/index.html exists'
                + ' and that no other index.html files are interfering with the build output at dist/index.html.');
            </script>
        </body>
        </html>
        """

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html", "Access-Control-Allow-Origin": "*"},
            "body": html_content,
        }


# Media files are byte-served in chunks so Lambda buffered responses stay below
# the 6MB payload limit after base64 encoding.
_FRONTEND_DIST_DIR = "/var/task/frontend/dist"
_MEDIA_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v", ".ogg", ".ogv", ".mp3", ".wav", ".m4a")
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_UNCOMPRESSIBLE_IMAGE_EXTENSIONS = (".gif", ".ico", ".svg")
_MAX_STATIC_CHUNK = 4 * 1024 * 1024
_MAX_IMAGE_RESPONSE_BYTES = 4 * 1024 * 1024
_IMAGE_CACHE_DIR = "/tmp/mgx-compressed-assets"
_IMAGE_COMPRESSION_CACHE_VERSION = "v1"


def serve_static_file(path: str, headers: dict = None) -> Dict[str, Any]:
    """Serve static files, with Range support for media."""
    content_types = {
        ".js": "application/javascript",
        ".css": "text/css",
        ".html": "text/html",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".ico": "image/x-icon",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".m4v": "video/x-m4v",
        ".ogg": "video/ogg",
        ".ogv": "video/ogg",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
    }

    ext = os.path.splitext(path)[1].lower()
    content_type = content_types.get(ext, "application/octet-stream")

    file_path = os.path.join(_FRONTEND_DIST_DIR, path.lstrip("/"))
    if not os.path.exists(file_path):
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "text/plain", "Access-Control-Allow-Origin": "*"},
            "body": "File not found",
        }

    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    range_header = headers.get("x-proxy-range") or headers.get("range")
    file_size = os.path.getsize(file_path)
    is_media = ext in _MEDIA_EXTENSIONS
    is_image = ext in _IMAGE_EXTENSIONS

    if not is_media and not range_header:
        if is_image and file_size > _MAX_IMAGE_RESPONSE_BYTES:
            return _serve_compressed_image(file_path, ext, headers)
        if ext in _UNCOMPRESSIBLE_IMAGE_EXTENSIONS and file_size > _MAX_IMAGE_RESPONSE_BYTES:
            return _asset_too_large()

        with open(file_path, "rb") as f:
            content = f.read()
        return {
            "statusCode": 200,
            "headers": {"Content-Type": content_type, "Access-Control-Allow-Origin": "*"},
            "body": content.decode("utf-8")
            if content_type.startswith("text/")
            else base64.b64encode(content).decode("utf-8"),
            "isBase64Encoded": not content_type.startswith("text/"),
        }

    if range_header:
        match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
        if not match:
            return _range_not_satisfiable(file_size, content_type)
        start_str, end_str = match.groups()
        if not start_str and not end_str:
            return _range_not_satisfiable(file_size, content_type)
        if start_str:
            start = int(start_str)
            end = int(end_str) if end_str else file_size - 1
        else:
            suffix_length = int(end_str)
            if suffix_length == 0:
                return _range_not_satisfiable(file_size, content_type)
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
        if start >= file_size or end < start:
            return _range_not_satisfiable(file_size, content_type)
    elif file_size > _MAX_STATIC_CHUNK:
        start = 0
        end = file_size - 1
    else:
        with open(file_path, "rb") as f:
            content = f.read()
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": content_type,
                "Access-Control-Allow-Origin": "*",
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
            "body": base64.b64encode(content).decode("utf-8"),
            "isBase64Encoded": True,
        }

    end = min(end, file_size - 1)
    if end - start + 1 > _MAX_STATIC_CHUNK:
        end = start + _MAX_STATIC_CHUNK - 1
    length = end - start + 1

    with open(file_path, "rb") as f:
        f.seek(start)
        content = f.read(length)

    return {
        "statusCode": 206,
        "headers": {
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "X-Auth-Token, X-Debug-Range, X-Debug-Header-Keys",
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
            "X-Debug-Range": str(range_header),
            "X-Debug-Header-Keys": ",".join(sorted(headers.keys())),
        },
        "body": base64.b64encode(content).decode("utf-8"),
        "isBase64Encoded": True,
    }


def _serve_compressed_image(file_path: str, source_ext: str, headers: dict) -> Dict[str, Any]:
    result = _get_compressed_image(file_path, source_ext, headers)
    if result is None:
        return _asset_too_large()

    content, content_type = result
    response_headers = {
        "Content-Type": content_type,
        "Access-Control-Allow-Origin": "*",
        "Content-Length": str(len(content)),
        "Cache-Control": "public, max-age=86400",
    }
    if source_ext == ".png":
        response_headers["Vary"] = "Accept"

    return {
        "statusCode": 200,
        "headers": response_headers,
        "body": base64.b64encode(content).decode("utf-8"),
        "isBase64Encoded": True,
    }


def _get_compressed_image(file_path: str, source_ext: str, headers: dict) -> tuple[bytes, str] | None:
    try:
        from PIL import Image, ImageOps
    except Exception as e:
        logger.warning(f"Pillow is unavailable, cannot compress image {file_path}: {e}")
        return None

    try:
        with Image.open(file_path) as original:
            if getattr(original, "is_animated", False):
                return None
            image = ImageOps.exif_transpose(original)
            output_format = _choose_image_output_format(image, source_ext, headers)
            if output_format is None:
                return None

            cache_path = _compressed_image_cache_path(file_path, output_format)
            cached = _read_cached_compressed_image(cache_path)
            if cached is not None:
                return cached, _image_content_type(output_format)

            prepared = _prepare_image_for_output(image, output_format)
            content = _compress_image_to_limit(prepared, output_format)
            if content is None:
                return None

            _write_cached_compressed_image(cache_path, content)
            return content, _image_content_type(output_format)
    except Exception as e:
        logger.warning(f"Failed to compress image {file_path}: {e}")
        return None


def _choose_image_output_format(image: Any, source_ext: str, headers: dict) -> str | None:
    if source_ext in (".jpg", ".jpeg"):
        return "JPEG"
    if source_ext == ".webp":
        return "WEBP"
    if source_ext == ".png":
        if _image_has_transparency(image):
            return "WEBP" if "image/webp" in headers.get("accept", "").lower() else None
        return "JPEG"
    return None


def _image_has_transparency(image: Any) -> bool:
    if image.mode in ("RGBA", "LA"):
        return image.getchannel("A").getextrema()[0] < 255
    if image.mode == "P":
        return "transparency" in image.info
    return False


def _prepare_image_for_output(image: Any, output_format: str) -> Any:
    from PIL import Image

    if output_format == "JPEG":
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return image.convert("RGB")

    if output_format == "WEBP":
        return image.convert("RGBA") if _image_has_transparency(image) else image.convert("RGB")

    return image


def _compress_image_to_limit(image: Any, output_format: str) -> bytes | None:
    from PIL import Image

    start_quality = 85 if output_format == "JPEG" else 82
    qualities = list(range(start_quality, 69, -5))
    current = image

    while True:
        for quality in qualities:
            content = _encode_image(current, output_format, quality)
            if len(content) <= _MAX_IMAGE_RESPONSE_BYTES:
                return content

        width, height = current.size
        long_edge = max(width, height)
        if long_edge <= 640:
            return None

        next_long_edge = max(640, int(long_edge * 0.85))
        scale = next_long_edge / long_edge
        next_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        current = current.resize(next_size, resampling)


def _encode_image(image: Any, output_format: str, quality: int) -> bytes:
    buffer = io.BytesIO()
    save_args = {"format": output_format, "quality": quality, "optimize": True}
    if output_format == "JPEG":
        save_args["progressive"] = True
    elif output_format == "WEBP":
        save_args["method"] = 6
    image.save(buffer, **save_args)
    return buffer.getvalue()


def _image_content_type(output_format: str) -> str:
    if output_format == "WEBP":
        return "image/webp"
    return "image/jpeg"


def _compressed_image_cache_path(file_path: str, output_format: str) -> str:
    stat = os.stat(file_path)
    cache_key = "|".join(
        [
            _IMAGE_COMPRESSION_CACHE_VERSION,
            file_path,
            str(stat.st_mtime_ns),
            str(stat.st_size),
            output_format,
            str(_MAX_IMAGE_RESPONSE_BYTES),
        ]
    )
    suffix = ".webp" if output_format == "WEBP" else ".jpg"
    return os.path.join(_IMAGE_CACHE_DIR, hashlib.sha256(cache_key.encode("utf-8")).hexdigest() + suffix)


def _read_cached_compressed_image(cache_path: str) -> bytes | None:
    try:
        if os.path.exists(cache_path) and os.path.getsize(cache_path) <= _MAX_IMAGE_RESPONSE_BYTES:
            with open(cache_path, "rb") as f:
                return f.read()
    except OSError:
        return None
    return None


def _write_cached_compressed_image(cache_path: str, content: bytes) -> None:
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp_path = f"{cache_path}.{os.getpid()}.tmp"
        with open(tmp_path, "wb") as f:
            f.write(content)
        os.replace(tmp_path, cache_path)
    except OSError as e:
        logger.warning(f"Failed to cache compressed image {cache_path}: {e}")


def _asset_too_large() -> Dict[str, Any]:
    body = "asset is too large for Lambda buffered response; compress or upload to object storage"
    return {
        "statusCode": 413,
        "headers": {
            "Content-Type": "text/plain",
            "Access-Control-Allow-Origin": "*",
            "Content-Length": str(len(body)),
        },
        "body": body,
    }


def _range_not_satisfiable(file_size: int, content_type: str) -> Dict[str, Any]:
    return {
        "statusCode": 416,
        "headers": {
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes */{file_size}",
        },
        "body": "Range Not Satisfiable",
    }


def handle_config_request(headers: dict, query_params: dict) -> Dict[str, Any]:
    """Handle configuration requests with security filtering"""
    # Security: Validate request method and origin
    validation_result = validate_config_request(headers)
    if not validation_result["isValid"]:
        return {
            "statusCode": 403,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": "Access denied", "message": "Invalid request"}),
        }

    # Security: Only return frontend-required configuration
    # Define what frontend actually needs (based on code analysis)
    frontend_required_config = {
        "API_BASE_URL": os.environ.get("VITE_API_BASE_URL", "http://127.0.0.1:8000")
        # Only add other configs if frontend actually uses them
        # DO NOT expose: VITE_FRONTEND_URL, secrets, internal URLs, etc.
    }

    # Security: Validate and sanitize the configuration
    sanitized_config = sanitize_config(frontend_required_config)
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=300",  # Cache for 5 minutes
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
        "body": json.dumps(sanitized_config),
    }


def validate_config_request(headers: dict) -> Dict[str, Any]:
    """Validate configuration request for security"""
    # Check for suspicious user agents (basic bot detection)
    user_agent = headers.get("user-agent", headers.get("User-Agent", ""))
    suspicious_patterns = ["bot", "crawler", "spider", "scraper", "curl", "wget"]

    if any(pattern in user_agent.lower() for pattern in suspicious_patterns):
        return {"isValid": False, "reason": "Suspicious user agent detected"}

    # Optional: Check referer for additional security (can be disabled if needed)
    referer = headers.get("referer", headers.get("Referer", ""))
    if referer and not is_valid_referer(referer):
        return {"isValid": False, "reason": "Invalid referer"}

    return {"isValid": True}


def is_valid_referer(referer: str) -> bool:
    """Check if referer is valid (optional security measure)"""
    # Allow requests from same domain or common frontend domains
    allowed_domains = [
        "localhost",
        "127.0.0.1",
        "amazonaws.com",
        "execute-api.us-east-1.amazonaws.com",
        "lambda-url.us-east-1.on.aws",
    ]
    env_allowed_domains = os.environ.get("ALLOWED_DOMAINS") or ""
    if env_allowed_domains:
        domains = [i.strip() for i in env_allowed_domains.split(",") if i.strip()]
        for domain in domains:
            if domain in allowed_domains:
                continue
            allowed_domains.append(domain)

    try:
        from urllib.parse import urlparse

        parsed_url = urlparse(referer)
        return any(domain in parsed_url.hostname for domain in allowed_domains if parsed_url.hostname)
    except:  # noqa: E722
        return False


def sanitize_config(config: dict) -> dict:
    """Sanitize configuration to ensure only safe, frontend-required data is exposed"""
    sanitized = {}

    # Define allowed configuration keys that frontend actually uses
    allowed_keys = [
        "API_BASE_URL"  # Only this is actually used by frontend
        # Add other keys only if frontend code actually uses them
    ]

    # Only include allowed keys with validation
    for key in allowed_keys:
        if key in config:
            # Additional validation for specific keys
            if key == "API_BASE_URL":
                # Ensure it's a valid URL format
                url = config[key]
                if isinstance(url, str) and (url.startswith("http://") or url.startswith("https://")):
                    sanitized[key] = url
                else:
                    logger.debug(f"Invalid API_BASE_URL format: {url}")
                    sanitized[key] = "http://127.0.0.1:8000"  # Safe fallback
            else:
                sanitized[key] = config[key]

    return sanitized


def replace_seo_domain(content: str, request_domain: str) -> str:
    """Replace SEO placeholder domain with actual request domain"""
    if request_domain and SEO_DOMAIN_PLACEHOLDER in content:
        return content.replace(SEO_DOMAIN_PLACEHOLDER, request_domain)
    return content


def serve_sitemap(request_domain: str = "") -> Dict[str, Any]:
    """Serve sitemap.xml file"""
    sitemap_path = "/var/task/frontend/dist/sitemap.xml"
    if not os.path.exists(sitemap_path):
        return {"statusCode": 404, "headers": {"Content-Type": "text/plain", "Access-Control-Allow-Origin": "*"}, "body": "sitemap.xml not found"}
    
    try:
        with open(sitemap_path, "r", encoding="utf-8") as f:
            content = replace_seo_domain(f.read(), request_domain)
        
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/xml", "Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
            "body": content,
        }
    except Exception as e:
        logger.error(f"Failed to read sitemap.xml: {e}")
        return {"statusCode": 500, "headers": {"Content-Type": "text/plain", "Access-Control-Allow-Origin": "*"}, "body": "Internal server error"}


def serve_robots(request_domain: str = "") -> Dict[str, Any]:
    """Serve robots.txt file"""
    robots_path = "/var/task/frontend/dist/robots.txt"
    if not os.path.exists(robots_path):
        return {"statusCode": 404, "headers": {"Content-Type": "text/plain", "Access-Control-Allow-Origin": "*"}, "body": "robots.txt not found"}

    try:
        with open(robots_path, "r", encoding="utf-8") as f:
            content = replace_seo_domain(f.read(), request_domain)
        
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/plain", "Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
            "body": content,
        }
    except Exception as e:
        logger.error(f"Failed to read robots.txt: {e}")
        return {"statusCode": 500, "headers": {"Content-Type": "text/plain", "Access-Control-Allow-Origin": "*"}, "body": "Internal server error"}


def serve_seo_html(path: str, request_domain: str = "") -> Dict[str, Any]:
    """Serve SEO HTML files from index.html"""
    html_path = f"/var/task/frontend/dist{path.rstrip('/')}/index.html"
    
    if not os.path.exists(html_path):
        return {"statusCode": 404, "headers": {"Content-Type": "text/html", "Access-Control-Allow-Origin": "*"}, "body": "<html><body><h1>404 Not Found</h1></body></html>"}
    
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = replace_seo_domain(f.read(), request_domain)
        content = _inject_overview_env(content)

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html", "Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
            "body": content,
        }
    except Exception as e:
        logger.error(f"Failed to read SEO HTML file {html_path}: {e}")
        return {"statusCode": 500, "headers": {"Content-Type": "text/html", "Access-Control-Allow-Origin": "*"}, "body": "<html><body><h1>500 Internal Server Error</h1></body></html>"}


# Export the handler for AWS Lambda
lambda_handler = summarize_lambda_handler(lambda_handler)


if __name__ == "__main__":
    # Test the handler locally
    test_event = {"httpMethod": "GET", "path": "/", "headers": {}, "body": ""}

    result = lambda_handler(test_event, None)
    print(json.dumps(result, indent=2))
