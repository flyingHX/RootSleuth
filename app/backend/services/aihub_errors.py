class InvalidImageInputError(ValueError):
    """Raised when the provided image input cannot be parsed."""


class InvalidAudioInputError(ValueError):
    """Raised when the provided audio input cannot be parsed."""


class InvalidPdfInputError(ValueError):
    """Raised when the provided PDF input is invalid or unsupported."""
