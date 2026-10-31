"""Document format readers and their normalized result types."""
from .adapters import (Attachment, DocumentData, DocumentReadError, html_to_text,
                       read_docx, read_image, read_pdf, read_source, read_xlsx)

__all__ = ["Attachment", "DocumentData", "DocumentReadError", "html_to_text",
           "read_docx", "read_image", "read_pdf", "read_source", "read_xlsx"]
