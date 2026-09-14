"""
AI Hub service layer implementation.
Provides text, image, video, and audio generation, PDF analysis,
plus speech transcription capabilities.
"""

import asyncio
import base64
import io
import json
import logging
from pathlib import Path
from typing import AsyncGenerator, Optional, TYPE_CHECKING

from core.config import settings
from schemas.aihub import AnalyzePdfRequest, AnalyzePdfResponse
from schemas.aihub import (
    GenAudioRequest,
    GenAudioResponse,
    GenImgRequest,
    GenImgResponse,
    GenTxtRequest,
    GenTxtResponse,
    GenVideoRequest,
    GenVideoResponse,
    TranscribeAudioRequest,
    TranscribeAudioResponse,
)
from services.aihub_errors import InvalidAudioInputError, InvalidImageInputError, InvalidPdfInputError

try:
    from core.telemetry import observe_external_http
except Exception:  # noqa: BLE001 - optional across mixed template versions
    async def observe_external_http(operation):
        return await operation


if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

PDF_ANALYSIS_MODEL = "claude-sonnet-4.6"
PDF_SYSTEM_PROMPT = """You are a careful PDF analysis assistant.

Rules:
- Answer only from the attached PDF.
- If the PDF does not contain the requested information, say so clearly.
- Do not invent or infer unsupported facts.
- Mention page numbers for important facts whenever the PDF makes that possible.
- Match the user's instruction language.
"""
PDF_MODE_PROMPTS = {
    "qa": """Task type: Question answering.
Read the attached PDF and answer the user's question directly, clearly, and only with information supported by the document.""",
    "extract": """Task type: Structured extraction.
Read the attached PDF and extract the requested information as concise Markdown with clear headings and bullets when helpful.""",
}
PDF_MAX_PAGE_WINDOW = 80
PDF_MAX_TOTAL_BYTES = 15 * 1024 * 1024
PDF_MAX_TOTAL_PAGES = 80


# Voice mapping: (model, gender) -> voice
VOICE_MAP: dict[tuple[str, str], str] = {
    # qwen3-tts-flash
    ("qwen3-tts-flash", "male"): "Ethan",
    ("qwen3-tts-flash", "female"): "Cherry",
    # gemini-2.5-pro-preview-tts
    ("gemini-2.5-pro-preview-tts", "male"): "Puck",
    ("gemini-2.5-pro-preview-tts", "female"): "Zephyr",
    # eleven
    ("eleven_v3", "male"): "echo",
    ("eleven_v3", "female"): "alloy",
    ("eleven_turbo_v2", "male"): "echo",
    ("eleven_turbo_v2", "female"): "alloy",
    # OpenAI gpt-4o-mini-tts
    ("gpt-4o-mini-tts", "male"): "echo",
    ("gpt-4o-mini-tts", "female"): "nova",
}
DEFAULT_VOICE = {"male": "Ethan", "female": "Cherry"}


class AIHubService:
    """AI Hub service class that wraps AI SDK calls."""

    def __init__(self):
        self.client: Optional["AsyncOpenAI"] = None
        if settings.app_ai_base_url and settings.app_ai_key:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(
                api_key=settings.app_ai_key,
                base_url=settings.app_ai_base_url.rstrip("/"),
            )

    def _require_ai_client(self) -> "AsyncOpenAI":
        """Return the configured AI client or raise a configuration error."""
        if not self.client:
            raise ValueError("AI service not configured. Set APP_AI_BASE_URL and APP_AI_KEY.")
        return self.client

    def _convert_message(self, msg) -> dict:
        """Convert message format and support multimodal content."""
        content = msg.content
        # If content is a list (multimodal), convert it to plain dicts
        if isinstance(content, list):
            content = [item.model_dump() if hasattr(item, "model_dump") else item for item in content]
        return {"role": msg.role, "content": content}

    async def gentxt(self, request: GenTxtRequest) -> GenTxtResponse:
        """
        Generate Text API (non-streaming), supports text and image input.

        Args:
            request: Generate text request parameters.

        Returns:
            Txt2TxtResponse: generated text response.
        """
        try:
            client = self._require_ai_client()
            messages = [self._convert_message(msg) for msg in request.messages]

            response = await observe_external_http(
                client.chat.completions.create(
                    model=request.model,
                    messages=messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    stream=False,
                )
            )

            content = response.choices[0].message.content or ""
            usage = None
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

            return GenTxtResponse(
                content=content,
                model=request.model,
                usage=usage,
            )

        except Exception as e:
            logger.error(f"gentxt error: {e}")
            raise

    async def gentxt_stream(self, request: GenTxtRequest) -> AsyncGenerator[str, None]:
        """
        Generate Text API (streaming), supports text and image input.

        Args:
            request: Generate text request parameters.

        Yields:
            str: Generated text content chunk (plain text, not JSON).
        """
        try:
            client = self._require_ai_client()
            messages = [self._convert_message(msg) for msg in request.messages]

            stream = await observe_external_http(
                client.chat.completions.create(
                    model=request.model,
                    messages=messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    stream=True,
                )
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error(f"gentxt_stream error: {e}")
            raise

    @staticmethod
    def _extract_image_ref(item: object) -> str:
        """
        Extract an image reference from an OpenAI-compatible genimg response item.

        Prefer `url` (to avoid huge response bodies); if url is not available, fall back to `b64_json`
        and wrap it as a base64 data URI.
        Compatible with both dict items and SDK object items.
        """
        if isinstance(item, dict):
            url = item.get("url")
            if url:
                return url
            b64_json = item.get("b64_json")
            if b64_json:
                return f"data:image/png;base64,{b64_json}"
        else:
            url = getattr(item, "url", None)
            if url:
                return url
            b64_json = getattr(item, "b64_json", None)
            if b64_json:
                return f"data:image/png;base64,{b64_json}"

        raise RuntimeError("Neither url nor b64_json found in genimg response item")

    @staticmethod
    def _parse_data_uri(data_uri: str) -> tuple[bytes, str]:
        """Parse a base64 data URI and return (bytes, content_type)."""
        if "," not in data_uri:
            raise InvalidImageInputError("Invalid data URI: missing ',' separator.")

        header, b64_data = data_uri.split(",", 1)
        content_type = "image/png"
        if header.startswith("data:"):
            meta = header[5:]
            # Typical header: "image/png;base64"
            if ";" in meta:
                maybe_type = meta.split(";", 1)[0].strip()
                if maybe_type:
                    content_type = maybe_type
            elif meta.strip():
                content_type = meta.strip()

        try:
            return base64.b64decode(b64_data), content_type
        except Exception as e:
            raise InvalidImageInputError("Invalid base64 data in data URI.") from e

    @staticmethod
    def _filename_from_content_type(content_type: str, name_prefix: str = "file", default_ext: str = "bin") -> str:
        """Best-effort filename for in-memory uploads."""
        ct = (content_type or "").lower()
        ext = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/webp": "webp",
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/mp4": "m4a",
            "audio/x-m4a": "m4a",
            "audio/webm": "webm",
            "audio/ogg": "ogg",
            "audio/flac": "flac",
        }.get(ct, default_ext)
        return f"{name_prefix}.{ext}"

    @staticmethod
    def _get_source_name(source_ref: str, fallback: str = "input_file") -> str:
        """Get a readable display name from a URL/path/data URI."""
        ref = (source_ref or "").strip()
        if ref.startswith(("http://", "https://")):
            return ref.split("?")[0].rstrip("/").split("/")[-1] or fallback
        if ref.startswith("data:"):
            return fallback
        return Path(ref).name or fallback

    async def _image_str_to_upload_file(self, image: str, name_prefix: str = "image") -> io.BytesIO:
        """
        Convert image input (base64 data URI or HTTP URL) into an in-memory file object for uploads.

        The OpenAI `images.edit` endpoint expects multipart file uploads; we keep the API JSON-only
        by allowing clients to pass a base64 data URI or HTTP URL, and converting it here.
        """
        image = (image or "").strip()
        if not image:
            raise InvalidImageInputError("Input image is empty.")

        # Handle HTTP URL: download content
        if image.startswith(("http://", "https://")):
            import httpx

            try:
                async with httpx.AsyncClient(timeout=60.0, trust_env=True) as client:
                    resp = await observe_external_http(client.get(image))
                    resp.raise_for_status()
                    image_bytes = resp.content

                # Extract filename from URL (fallback if missing)
                name = image.split("?")[0].rstrip("/").split("/")[-1] or f"{name_prefix}.png"
                upload = io.BytesIO(image_bytes)
                upload.name = name  # type: ignore[attr-defined]
                return upload
            except Exception as e:
                raise InvalidImageInputError(f"Failed to download image from URL: {e}") from e

        if not image.startswith("data:"):
            raise InvalidImageInputError(
                "Only base64 data URI or HTTP URL is supported. Example: `data:image/png;base64,...` or `https://...`."
            )

        image_bytes, content_type = self._parse_data_uri(image)

        upload = io.BytesIO(image_bytes)
        # openai SDK uses this name for multipart filename
        upload.name = self._filename_from_content_type(  # type: ignore[attr-defined]
            content_type,
            name_prefix=name_prefix,
            default_ext="png",
        )
        return upload

    async def _image_input_to_upload_files(self, image_input: str | list[str]) -> list[io.BytesIO]:
        """
        Convert image input (single data URI or list of data URIs) into uploadable file objects.

        Some OpenAI-compatible `images/edits` implementations support multiple input images.
        """
        images = [image_input] if isinstance(image_input, str) else image_input
        if not images:
            raise InvalidImageInputError("Input image list is empty.")

        upload_files: list[io.BytesIO] = []
        for idx, img in enumerate(images):
            if not isinstance(img, str):
                raise InvalidImageInputError("Each image must be a base64 data URI string.")
            upload_files.append(await self._image_str_to_upload_file(img, name_prefix=f"image_{idx + 1}"))
        return upload_files

    async def _audio_str_to_upload_file(self, audio: str, name_prefix: str = "audio") -> io.BytesIO:
        """
        Convert audio input (base64 data URI, HTTP URL, or absolute path) into an in-memory file object.

        This keeps the API JSON-only while still supporting OpenAI-compatible multipart upload semantics.
        """
        audio = (audio or "").strip()
        if not audio:
            raise InvalidAudioInputError("Input audio is empty.")

        if audio.startswith(("http://", "https://")):
            import httpx

            try:
                async with httpx.AsyncClient(timeout=120.0, trust_env=True) as client:
                    resp = await observe_external_http(client.get(audio))
                    resp.raise_for_status()
                    audio_bytes = resp.content
                name = self._get_source_name(audio, fallback=f"{name_prefix}.mp3")
                upload = io.BytesIO(audio_bytes)
                upload.name = name  # type: ignore[attr-defined]
                return upload
            except Exception as e:
                raise InvalidAudioInputError(f"Failed to download audio from URL: {e}") from e

        if audio.startswith("data:"):
            audio_bytes, content_type = self._parse_data_uri(audio)
            upload = io.BytesIO(audio_bytes)
            upload.name = self._filename_from_content_type(  # type: ignore[attr-defined]
                content_type,
                name_prefix=name_prefix,
                default_ext="mp3",
            )
            return upload

        path = Path(audio).expanduser()
        if not path.is_absolute():
            raise InvalidAudioInputError(
                "Only absolute path, http(s) URL, or base64 data URI is supported for audio input."
            )
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {str(path)}")

        upload = io.BytesIO(path.read_bytes())
        upload.name = path.name  # type: ignore[attr-defined]
        return upload

    @staticmethod
    def _extract_transcription_text(resp: object) -> Optional[str]:
        """Extract transcription text from SDK response."""
        if isinstance(resp, str) and resp.strip():
            return resp.strip()

        if isinstance(resp, dict):
            text = resp.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
            content = resp.get("content")
        else:
            text = getattr(resp, "text", None)
            if isinstance(text, str) and text.strip():
                return text.strip()
            content = getattr(resp, "content", None)

        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")

        if isinstance(content, dict):
            data = content
        elif isinstance(content, str) and content.strip():
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return None
        else:
            return None

        text = data.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        return None

    @staticmethod
    def _parse_base64_data_uri(data_uri: str, *, error_cls: type[ValueError]) -> tuple[bytes, str]:
        """Parse a base64 data URI and return decoded bytes plus content type."""
        if "," not in data_uri:
            raise error_cls("Invalid data URI: missing ',' separator.")

        header, b64_data = data_uri.split(",", 1)
        content_type = "application/octet-stream"
        if header.startswith("data:"):
            meta = header[5:]
            if ";" in meta:
                maybe_type = meta.split(";", 1)[0].strip()
                if maybe_type:
                    content_type = maybe_type
            elif meta.strip():
                content_type = meta.strip()

        try:
            return base64.b64decode(b64_data), content_type
        except Exception as exc:
            raise error_cls("Invalid base64 data in data URI.") from exc

    @staticmethod
    def _extract_chat_text_content(content: object) -> str:
        """Extract text from OpenAI-compatible chat message content."""
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                else:
                    text = getattr(item, "text", None)
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            return "\n".join(parts).strip()

        return ""

    @classmethod
    def _extract_completion_text(cls, response: object) -> str:
        """Extract the first completion text from a chat completion response."""
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        content = getattr(message, "content", None) if message else None
        return cls._extract_chat_text_content(content)

    @staticmethod
    def _build_pdf_user_prompt(instruction: str, mode: str) -> str:
        return f"""{PDF_MODE_PROMPTS[mode]}

User instruction:
{instruction.strip()}
"""

    @staticmethod
    def _build_pdf_success_message(page_start: int, page_end: int, total_pages: int) -> str:
        selected_range = f"page {page_start}" if page_start == page_end else f"pages {page_start}-{page_end}"
        total_label = "page" if total_pages == 1 else "pages"
        return f"PDF analyzed successfully using {selected_range} of {total_pages} total {total_label}."

    @staticmethod
    def _resolve_pdf_page_range(
        total_pages: int,
        page_start: int = 1,
        page_end: Optional[int] = None,
    ) -> tuple[int, int]:
        if total_pages <= 0:
            raise InvalidPdfInputError("PDF has no pages.")
        if page_start < 1:
            raise InvalidPdfInputError("page_start must be greater than or equal to 1.")
        if page_start > total_pages:
            raise InvalidPdfInputError(f"page_start {page_start} exceeds total PDF pages {total_pages}.")

        if page_end is None:
            page_end = min(total_pages, page_start + PDF_MAX_PAGE_WINDOW - 1)

        if page_end < page_start:
            raise InvalidPdfInputError("page_end must be greater than or equal to page_start.")
        if page_end > total_pages:
            raise InvalidPdfInputError(f"page_end {page_end} exceeds total PDF pages {total_pages}.")

        selected_pages = page_end - page_start + 1
        if selected_pages > PDF_MAX_PAGE_WINDOW:
            raise InvalidPdfInputError(
                f"Requested page range contains {selected_pages} pages. "
                f"The maximum supported range per request is {PDF_MAX_PAGE_WINDOW} pages."
            )

        return page_start, page_end

    @staticmethod
    def _validate_pdf_attachment_limits(pdf_bytes: bytes, page_count: int) -> None:
        if len(pdf_bytes) <= PDF_MAX_TOTAL_BYTES and page_count <= PDF_MAX_TOTAL_PAGES:
            return

        size_mb = len(pdf_bytes) / 1024 / 1024
        raise InvalidPdfInputError(
            "PDF exceeds native attachment limits: "
            f"{size_mb:.2f}MB and {page_count} pages "
            "(limits: 15MB total, 80 pages total)."
        )

    @classmethod
    def _prepare_pdf_attachment(
        cls,
        pdf_bytes: bytes,
        page_start: int = 1,
        page_end: Optional[int] = None,
    ) -> tuple[str, int, int, int]:
        import fitz

        try:
            source_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise InvalidPdfInputError("Failed to read the provided PDF document.") from exc

        try:
            total_pages = source_doc.page_count
            start, end = cls._resolve_pdf_page_range(total_pages=total_pages, page_start=page_start, page_end=page_end)
            subset_doc = fitz.open()
            try:
                subset_doc.insert_pdf(source_doc, from_page=start - 1, to_page=end - 1)
                subset_bytes = subset_doc.tobytes(garbage=4, deflate=True)
            finally:
                subset_doc.close()
        finally:
            source_doc.close()

        cls._validate_pdf_attachment_limits(subset_bytes, end - start + 1)
        return base64.b64encode(subset_bytes).decode("utf-8"), start, end, total_pages

    async def _pdf_source_to_bytes(self, pdf: str) -> tuple[bytes, str]:
        """Resolve a PDF data URI into raw bytes and a readable file name."""
        pdf = (pdf or "").strip()
        if not pdf:
            raise InvalidPdfInputError("PDF input is empty.")

        if not pdf.startswith("data:"):
            raise InvalidPdfInputError(
                "Only base64 PDF data URI is supported for PDF input. Example: `data:application/pdf;base64,...`."
            )

        pdf_bytes, content_type = self._parse_base64_data_uri(pdf, error_cls=InvalidPdfInputError)
        if content_type.lower() != "application/pdf":
            raise InvalidPdfInputError("PDF data URI must use content type `application/pdf`.")

        return pdf_bytes, self._get_source_name(pdf, fallback="document.pdf")

    async def analyze_pdf(self, request: AnalyzePdfRequest) -> AnalyzePdfResponse:
        """Analyze a single PDF with native PDF input."""
        if not request.instruction or not request.instruction.strip():
            raise InvalidPdfInputError("instruction is required for PDF analysis.")

        client = self._require_ai_client()
        pdf_bytes, pdf_name = await self._pdf_source_to_bytes(request.pdf)
        pdf_b64, start, end, total_pages = self._prepare_pdf_attachment(
            pdf_bytes=pdf_bytes,
            page_start=request.page_start,
            page_end=request.page_end,
        )
        user_prompt = self._build_pdf_user_prompt(request.instruction, request.mode)
        response = await observe_external_http(
            client.chat.completions.create(
                model=PDF_ANALYSIS_MODEL,
                messages=[
                    {"role": "system", "content": PDF_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": pdf_b64,
                                },
                                "citations": {"enabled": True},
                            },
                        ],
                    },
                ],
                temperature=0.0,
                max_tokens=8192,
                stream=False,
            )
        )
        result = self._extract_completion_text(response)
        if not result:
            raise RuntimeError("PDF analysis returned an empty result.")

        return AnalyzePdfResponse(
            status="success",
            result=result,
            message=self._build_pdf_success_message(start, end, total_pages),
            pdf_name=pdf_name,
            mode=request.mode,
            model=PDF_ANALYSIS_MODEL,
            page_start=start,
            page_end=end,
            total_pages=total_pages,
        )

    async def genimg(self, request: GenImgRequest) -> GenImgResponse:
        """
        Generate Image API.

        Args:
            request: Generate image request parameters.

        Returns:
            GenImgResponse: generated image response, where `images` is a list of image refs (URL preferred; fallback to base64 data URI).
        """
        try:
            client = self._require_ai_client()
            # If an input image is provided, use the image editing endpoint (img2img).
            if request.image:
                image_files = await self._image_input_to_upload_files(request.image)
                image_param = image_files[0] if len(image_files) == 1 else image_files
                response = await observe_external_http(
                    client.images.edit(
                        model=request.model,
                        image=image_param,
                        prompt=request.prompt,
                        size=request.size,
                        n=request.n,
                        response_format=request.response_format,
                    )
                )
            else:
                response = await observe_external_http(
                    client.images.generate(
                        model=request.model,
                        prompt=request.prompt,
                        size=request.size,
                        quality=request.quality,
                        n=request.n,
                        response_format=request.response_format,
                    )
                )

            revised_prompt = response.data[0].revised_prompt if response.data else None

            if not response.data:
                raise RuntimeError("Image generation returned empty result")

            # Prefer URL to avoid huge response bodies; fallback to base64 data URI.
            images = [self._extract_image_ref(item) for item in response.data]

            return GenImgResponse(
                images=images,
                model=request.model,
                revised_prompt=revised_prompt,
            )

        except Exception as e:
            logger.error(f"genimg error: {e}")
            raise

    @staticmethod
    def _safe_int(value: object, default: int) -> int:
        """Best-effort convert to int, fallback to default."""
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _extract_cdn_url(obj: object) -> Optional[str]:
        """
        Extract CDN URL from response object (supports multiple platform formats).
        Works for both video and audio responses.
        """
        # Try: obj.url
        url = getattr(obj, "url", None)
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url

        # Try: obj.videos[0].url (video format)
        videos = getattr(obj, "videos", None)
        if videos and isinstance(videos, (list, tuple)) and len(videos) > 0:
            out_url = getattr(videos[0], "url", None)
            if isinstance(out_url, str) and out_url.startswith(("http://", "https://")):
                return out_url

        # Try: obj.video_url or obj.audio_url
        for attr in ("video_url", "audio_url"):
            attr_url = getattr(obj, attr, None)
            if isinstance(attr_url, str) and attr_url.startswith(("http://", "https://")):
                return attr_url

        # Try: obj.output.url
        output = getattr(obj, "output", None)
        if output:
            out_url = getattr(output, "url", None)
            if isinstance(out_url, str) and out_url.startswith(("http://", "https://")):
                return out_url

        # Try: obj.meta_data['url']
        meta_data = getattr(obj, "meta_data", None)
        if isinstance(meta_data, dict):
            meta_url = meta_data.get("url")
            if isinstance(meta_url, str) and meta_url.startswith(("http://", "https://")):
                return meta_url

        # Try parsing JSON body from HttpxBinaryResponseContent (proxy platform returns JSON instead of binary)
        try:
            data = json.loads(getattr(obj, "content", b""))
            logger.debug(f"Parsed response JSON body: {data}")
            for key in ("url", "video_url", "audio_url"):
                val = data.get(key)
                if isinstance(val, str) and val.startswith(("http://", "https://")):
                    return val
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        return None

    async def genvideo(self, request: GenVideoRequest) -> GenVideoResponse:
        """
        Generate Video API.

        Flow: 1) Create task -> 2) Poll until complete -> 3) Return CDN URL.
        Note: Different models have different `seconds` param support.
        """
        try:
            client = self._require_ai_client()
            create_params: dict[str, object] = {
                "model": request.model,
                "prompt": request.prompt,
                "size": request.size,
                "seconds": request.seconds
            }

            # Image-to-Video: use input_reference as the first frame
            if request.image:
                create_params["input_reference"] = await self._image_str_to_upload_file(
                    request.image, name_prefix="input_reference"
                )

            video = await observe_external_http(
                client.videos.create(**create_params)  # type: ignore[arg-type]
            )
            video_id = getattr(video, "id", None)
            if not video_id:
                raise RuntimeError("Video generation started but missing video id")

            logger.info(f"Video generation started: {video_id}")

            # Poll for completion
            status = getattr(video, "status", None)
            while status in ("in_progress", "queued"):
                logger.info(f"Video {video_id} progress: {getattr(video, 'progress', 0)}%")
                await asyncio.sleep(2)
                video = await observe_external_http(client.videos.retrieve(video_id))
                status = getattr(video, "status", None)

            if status == "failed":
                error_msg = getattr(getattr(video, "error", None), "message", None) or "Video generation failed"
                raise RuntimeError(error_msg)

            # Extract CDN URL
            cdn_url = self._extract_cdn_url(video)
            if not cdn_url:
                raise RuntimeError("Video generation completed but missing CDN url")

            requested_seconds = self._safe_int(request.seconds, default=4)
            actual_duration = self._safe_int(getattr(video, "seconds", None), default=requested_seconds)

            logger.info(f"Video generated: {cdn_url}")

            return GenVideoResponse(
                url=cdn_url,
                model=request.model,
                duration=actual_duration,
                revised_prompt=getattr(video, "revised_prompt", None),
            )

        except Exception as e:
            logger.error(f"genvideo error: {e}")
            raise

    @staticmethod
    def _get_voice(model: str, gender: str) -> str:
        """Get voice based on model and gender from mapping table."""
        voice = VOICE_MAP.get((model, gender))
        if voice:
            return voice
        return DEFAULT_VOICE.get(gender, "alloy")

    async def genaudio(self, request: GenAudioRequest) -> GenAudioResponse:
        """Generate Audio (TTS) API using OpenAI-compatible endpoint."""
        try:
            client = self._require_ai_client()
            voice = self._get_voice(request.model, request.gender)
            params: dict[str, object] = {
                "model": request.model,
                "input": request.text,
                "voice": voice,
                "response_format": "mp3",
            }

            logger.info(f"Audio generation started: model={request.model}, gender={request.gender}, voice={voice}")

            resp = await observe_external_http(
                client.audio.speech.create(**params)  # type: ignore[arg-type]
            )

            cdn_url = self._extract_cdn_url(resp)
            if not cdn_url:
                try:
                    body = getattr(resp, "content", resp)
                except Exception:
                    body = str(resp)
                logger.warning(f"Failed to extract CDN URL from audio response, body={body}")
                raise RuntimeError("Audio generation completed but missing CDN url")

            logger.info(f"Audio generated: {cdn_url}")

            return GenAudioResponse(
                url=cdn_url,
                model=request.model,
                gender=request.gender,
                voice=voice,
            )

        except Exception as e:
            logger.error(f"genaudio error: {e}")
            raise

    async def transcribe(self, request: TranscribeAudioRequest) -> TranscribeAudioResponse:
        """Transcribe audio to text using OpenAI-compatible speech transcription endpoint."""
        source_name = self._get_source_name(request.audio, fallback="input_audio")
        audio_file = await self._audio_str_to_upload_file(request.audio, name_prefix="input_audio")

        try:
            client = self._require_ai_client()
            logger.info(f"Audio transcription started: model={request.model}, source={source_name}")
            resp = await observe_external_http(
                client.audio.transcriptions.create(
                    file=audio_file,
                    model=request.model,
                    response_format="json",
                )
            )

            text = self._extract_transcription_text(resp)
            if not text:
                raise RuntimeError("Audio transcription completed but missing text in response")

            logger.info(f"Audio transcribed: {source_name}")

            return TranscribeAudioResponse(
                text=text,
                model=request.model,
                source_name=source_name,
            )
        except Exception as e:
            logger.error(f"transcribe error: {e}")
            raise
        finally:
            audio_file.close()
