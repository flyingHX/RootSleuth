"""
Request and response models for the AI Hub module.
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field

# ==================== Generate Text ====================


class ImageUrl(BaseModel):
    """Image URL configuration."""

    url: str = Field(..., description="Image URL or base64 data URI.")


class ContentPartText(BaseModel):
    """Text content part."""

    type: str = Field(default="text", description="Content type.")
    text: str = Field(..., description="Text content.")


class ContentPartImage(BaseModel):
    """Image content part."""

    type: str = Field(default="image_url", description="Content type.")
    image_url: ImageUrl = Field(..., description="Image URL configuration.")


class ChatMessage(BaseModel):
    """
    Chat message format.

    Supports two `content` formats:
    1. Plain text: content = "Hello"
    2. Multimodal: content = [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "..."}}]
    """

    role: str = Field(..., description="Message role: system/user/assistant.")
    content: Union[str, List[Union[ContentPartText, ContentPartImage]]] = Field(
        ..., description="Message content: a string or a list of content parts (multimodal)."
    )


class GenTxtRequest(BaseModel):
    """Generate Text request parameters."""

    messages: List[ChatMessage] = Field(..., description="Conversation messages list.")
    model: str = Field(default="deepseek-v4-pro", description="Model name")
    stream: bool = Field(default=False, description="Whether to enable streaming output.")
    temperature: Optional[float] = Field(default=0.7, description="Sampling temperature (0-2).")
    max_tokens: Optional[int] = Field(default=4096, description="Maximum number of generated tokens.")


class GenTxtResponse(BaseModel):
    """Generate Text response (non-streaming)."""

    content: str = Field(..., description="Generated text content.")
    model: str = Field(..., description="Name of the model used.")
    usage: Optional[dict] = Field(default=None, description="Token usage statistics.")


# ==================== Generate Image ====================


class GenImgRequest(BaseModel):
    """Generate Image request parameters."""

    prompt: str = Field(..., description="Prompt for image generation.")
    image: Optional[Union[str, List[str]]] = Field(
        default=None,
        description=(
            "Optional input image(s) for editing (base64 data URI). "
            "Supports a single string or a list of strings, e.g. `data:image/png;base64,...` or [`data:...`, `data:...`]. "
            "If provided, the API performs image editing (img2img) instead of text-to-image."
        ),
    )
    model: str = Field(
        default="gpt-image-2",
        description="Model name",
    )
    size: str = Field(default="1024x1024", description="Image size: 1024x1024 / 1024x1792 / 1792x1024.")
    quality: Literal["auto", "hd"] = Field(
        default="auto",
        description="Image quality (only for text-to-image; ignored when `image` is provided).",
    )
    n: int = Field(default=1, description="Number of images to generate (1-4).")
    response_format: str = Field(default="url", description="default response format")


class GenImgResponse(BaseModel):
    """Generate Image response."""

    images: List[str] = Field(
        ...,
        description=(
            "Generated image references list. Prefer HTTP URL to avoid huge response bodies; "
            "fallback to base64 data URI when url is not available."
        ),
    )
    model: str = Field(..., description="Name of the model used.")
    revised_prompt: Optional[str] = Field(default=None, description="Refined prompt used for generation.")


# ==================== Generate Video ====================


class GenVideoRequest(BaseModel):
    """
    Generate Video request parameters.

    Note: Different models have different size/duration constraints.
    """

    prompt: str = Field(..., description="Prompt for video generation.")
    image: Optional[str] = Field(
        default=None,
        description=(
            "Optional input image reference (base64 data URI) to guide generation (image-to-video). "
            "If provided, it acts as the first frame / reference asset for the output video. "
            "Example: `data:image/jpeg;base64,...`"
        ),
    )
    model: str = Field(default="wan2.6-t2v", description="Video generation model name.")
    size: str = Field(default="1280x720", description="Resolution (720p). Do NOT change unless specifically required.")
    seconds: str = Field(default="4", description="Duration in seconds. Do NOT change unless specifically required.")


class GenVideoResponse(BaseModel):
    """Generate Video response."""

    url: str = Field(..., description="CDN URL of the generated video file.")
    model: str = Field(..., description="Name of the model used.")
    duration: int = Field(..., description="Video duration in seconds.")
    revised_prompt: Optional[str] = Field(default=None, description="Refined prompt used for generation.")


# ==================== Generate Audio ====================


class GenAudioRequest(BaseModel):
    """Generate Audio (TTS) request parameters."""

    text: str = Field(..., description="Text content to convert to audio (TTS input).")
    model: str = Field(
        default="qwen3-tts-flash",
        description="TTS model name.",
    )
    gender: Literal["male", "female"] = Field(
        default="female",
        description="Voice gender: male or female.",
    )


class GenAudioResponse(BaseModel):
    """Generate Audio response."""

    url: str = Field(..., description="CDN URL of the generated audio file.")
    model: str = Field(..., description="Name of the model used.")
    gender: str = Field(..., description="Voice gender used for generation.")
    voice: str = Field(..., description="Actual voice used for generation.")


# ==================== Analyze PDF ====================


class AnalyzePdfRequest(BaseModel):
    """Single PDF analysis request parameters."""

    pdf: str = Field(
        ...,
        description="PDF source as a base64 data URI, e.g. data:application/pdf;base64,...",
    )
    instruction: str = Field(..., description="Question or extraction instruction for the PDF.")
    mode: Literal["qa", "extract"] = Field(
        default="qa",
        description="Analysis mode: qa or extract.",
    )
    page_start: int = Field(default=1, description="1-based start page.")
    page_end: Optional[int] = Field(
        default=None,
        description="1-based end page. If omitted, analyzes up to 80 pages starting from page_start.",
    )


class AnalyzePdfResponse(BaseModel):
    """Single PDF analysis response."""

    status: str = Field(..., description="Result status.")
    result: str = Field(..., description="Generated answer or extracted content.")
    message: str = Field(..., description="Human-readable status message.")
    pdf_name: str = Field(..., description="Readable PDF file name.")
    mode: str = Field(..., description="Analysis mode used.")
    model: str = Field(..., description="Model used for PDF analysis.")
    page_start: int = Field(..., description="Resolved 1-based start page.")
    page_end: Optional[int] = Field(default=None, description="Resolved 1-based end page.")
    total_pages: Optional[int] = Field(default=None, description="Total pages in the source PDF.")
    error_type: Optional[str] = Field(default=None, description="Error type when the request fails.")

# ==================== Transcribe Audio ====================


class TranscribeAudioRequest(BaseModel):
    """Speech transcription request parameters."""

    audio: str = Field(
        ...,
        description="Audio source. Supports absolute path, http(s) URL, or base64 data URI.",
    )
    model: str = Field(
        default="scribe_v2",
        description="Speech transcription model name.",
    )


class TranscribeAudioResponse(BaseModel):
    """Speech transcription response."""

    text: str = Field(..., description="Transcribed text.")
    model: str = Field(..., description="Name of the model used.")
    source_name: Optional[str] = Field(
        default=None,
        description="Readable source name extracted from the input audio reference.",
    )
