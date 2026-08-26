"""업로드한 문서를 실행하지 않고 텍스트 블록과 원문 위치로 변환한다."""

import base64
import io
import re
import zipfile
from collections.abc import Sequence
from typing import Protocol
from xml.etree import ElementTree

import httpx
from pypdf import PdfReader

from app.documents.models import DocumentAnchor, DocumentRevision, ParserBlock, ParserResultRequest
from app.documents.storage import AccessTokenProvider, GoogleAccessTokenProvider

MAX_BLOCK_TEXT = 12_000
VISION_FILE_PAGE_BATCH_SIZE = 5
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocumentParseError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OcrClient(Protocol):
    def extract_pages(self, *, content: bytes, content_type: str) -> list[str]: ...


class GoogleVisionOcrClient:
    """이미지와 텍스트가 없는 PDF를 Cloud Vision OCR로 읽는다."""

    def __init__(
        self,
        *,
        access_tokens: AccessTokenProvider | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._access_tokens = access_tokens or GoogleAccessTokenProvider()
        self._client = client or httpx.Client(timeout=30.0)

    def extract_pages(self, *, content: bytes, content_type: str) -> list[str]:
        encoded = base64.b64encode(content).decode("ascii")
        headers = {"Authorization": f"Bearer {self._access_tokens.token()}"}
        if content_type == "application/pdf":
            try:
                page_count = len(PdfReader(io.BytesIO(content)).pages)
            except Exception as error:
                raise DocumentParseError("VISION_OCR_PDF_INVALID") from error
            texts: list[str] = []
            for start in range(1, page_count + 1, VISION_FILE_PAGE_BATCH_SIZE):
                page_numbers = list(
                    range(
                        start,
                        min(start + VISION_FILE_PAGE_BATCH_SIZE, page_count + 1),
                    )
                )
                payload = self._post_json(
                    "https://vision.googleapis.com/v1/files:annotate",
                    headers=headers,
                    body={
                        "requests": [
                            {
                                "inputConfig": {
                                    "content": encoded,
                                    "mimeType": content_type,
                                },
                                "features": [
                                    {"type": "DOCUMENT_TEXT_DETECTION"}
                                ],
                                "pages": page_numbers,
                            }
                        ]
                    },
                )
                outer = payload.get("responses")
                if not isinstance(outer, list) or len(outer) != 1:
                    raise DocumentParseError("VISION_OCR_RESPONSE_INVALID")
                file_response = outer[0]
                if not isinstance(file_response, dict):
                    raise DocumentParseError("VISION_OCR_RESPONSE_INVALID")
                texts.extend(
                    self._vision_texts(
                        file_response.get("responses"), expected=len(page_numbers)
                    )
                )
            return texts
        payload = self._post_json(
            "https://vision.googleapis.com/v1/images:annotate",
            headers=headers,
            body={
                "requests": [
                    {
                        "image": {"content": encoded},
                        "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    }
                ]
            },
        )
        return self._vision_texts(payload.get("responses"), expected=1)

    def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> dict[str, object]:
        try:
            response = self._client.post(url, headers=headers, json=body)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise DocumentParseError("VISION_OCR_REQUEST_FAILED") from error
        try:
            payload = response.json()
        except ValueError as error:
            raise DocumentParseError("VISION_OCR_RESPONSE_INVALID") from error
        if not isinstance(payload, dict):
            raise DocumentParseError("VISION_OCR_RESPONSE_INVALID")
        return payload

    @classmethod
    def _vision_texts(cls, values: object, *, expected: int) -> list[str]:
        if not isinstance(values, list) or len(values) != expected:
            raise DocumentParseError("VISION_OCR_RESPONSE_INVALID")
        texts: list[str] = []
        for value in values:
            if not isinstance(value, dict):
                raise DocumentParseError("VISION_OCR_RESPONSE_INVALID")
            if value.get("error"):
                raise DocumentParseError("VISION_OCR_PAGE_FAILED")
            texts.append(cls._vision_text(value))
        return texts

    @staticmethod
    def _vision_text(value: object) -> str:
        if not isinstance(value, dict):
            return ""
        annotation = value.get("fullTextAnnotation")
        if not isinstance(annotation, dict):
            return ""
        text = annotation.get("text")
        return text.strip() if isinstance(text, str) else ""


class OperationalDocumentParser:
    def __init__(self, *, ocr: OcrClient | None = None) -> None:
        self._ocr = ocr

    def parse(self, revision: DocumentRevision, content: bytes) -> ParserResultRequest:
        pages = self._extract_pages(content=content, content_type=revision.content_type)
        blocks = self._blocks(revision.document_revision_id, pages)
        if not blocks:
            raise DocumentParseError("DOCUMENT_TEXT_NOT_FOUND")
        return ParserResultRequest(
            project_id=revision.project_id,
            document_id=revision.document_id,
            parser_version="caffemate-operational-parser.v1",
            blocks=blocks,
            prompt_injection_flags=self._prompt_injection_flags(pages),
        )

    def _extract_pages(self, *, content: bytes, content_type: str) -> list[str]:
        if content_type == "application/pdf":
            try:
                reader = PdfReader(io.BytesIO(content))
                pages = [(page.extract_text() or "").strip() for page in reader.pages]
            except Exception as error:
                raise DocumentParseError("PDF_PARSE_FAILED") from error
            if any(pages):
                return pages
            if self._ocr is not None:
                return self._ocr.extract_pages(content=content, content_type=content_type)
            return []
        if content_type == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            return [self._docx_text(content)]
        if content_type in {"image/jpeg", "image/png"}:
            if self._ocr is None:
                raise DocumentParseError("OCR_NOT_CONFIGURED")
            return self._ocr.extract_pages(content=content, content_type=content_type)
        raise DocumentParseError("DOCUMENT_TYPE_NOT_SUPPORTED")

    @staticmethod
    def _docx_text(content: bytes) -> str:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                xml = archive.read("word/document.xml")
        except (KeyError, zipfile.BadZipFile) as error:
            raise DocumentParseError("DOCX_PARSE_FAILED") from error
        root = ElementTree.fromstring(xml)
        paragraphs: list[str] = []
        for paragraph in root.iter(f"{_WORD_NS}p"):
            text = "".join(node.text or "" for node in paragraph.iter(f"{_WORD_NS}t")).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs)

    @staticmethod
    def _blocks(document_revision_id: str, pages: Sequence[str]) -> list[ParserBlock]:
        blocks: list[ParserBlock] = []
        for page_index, page in enumerate(pages):
            normalized = re.sub(r"[ \t]+", " ", page).strip()
            for chunk_index, start in enumerate(range(0, len(normalized), MAX_BLOCK_TEXT)):
                text = normalized[start : start + MAX_BLOCK_TEXT].strip()
                if not text:
                    continue
                blocks.append(
                    ParserBlock(
                        block_id=f"page-{page_index + 1}-chunk-{chunk_index + 1}",
                        text=text,
                        anchor=DocumentAnchor(
                            document_revision_id=document_revision_id,
                            page_index=page_index,
                            section_path=f"{page_index + 1}쪽",
                        ),
                    )
                )
        return blocks

    @staticmethod
    def _prompt_injection_flags(pages: Sequence[str]) -> list[str]:
        text = "\n".join(pages).lower()
        patterns = (
            "ignore previous instructions",
            "ignore all instructions",
            "system prompt",
            "이전 지시를 무시",
            "모든 지시를 무시",
            "시스템 프롬프트",
        )
        if any(value in text for value in patterns):
            return ["DOCUMENT_PROMPT_INJECTION_SUSPECTED"]
        return []


def active_content_threats(*, content: bytes, content_type: str) -> list[str]:
    if content_type == "application/pdf" and any(
        marker in content for marker in (b"/JavaScript", b"/Launch", b"/EmbeddedFile")
    ):
        return ["PDF_ACTIVE_CONTENT"]
    if content_type.endswith("wordprocessingml.document"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = {name.lower() for name in archive.namelist()}
        except zipfile.BadZipFile:
            return ["DOCX_CONTAINER_INVALID"]
        if any("vbaproject" in name or name.startswith("word/embeddings/") for name in names):
            return ["DOCX_ACTIVE_CONTENT"]
    return []
