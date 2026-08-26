import json

import httpx

from app.documents.parser import DocumentParseError, GoogleVisionOcrClient


class FixedAccessTokenProvider:
    def token(self) -> str:
        return "vision-access-token"


def test_image_ocr_uses_document_text_detection_and_preserves_text() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "responses": [
                    {"fullTextAnnotation": {"text": "보증금 3,000만원\n월세 220만원"}}
                ]
            },
        )

    client = GoogleVisionOcrClient(
        access_tokens=FixedAccessTokenProvider(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    pages = client.extract_pages(content=b"png-content", content_type="image/png")

    assert pages == ["보증금 3,000만원\n월세 220만원"]
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/images:annotate"
    assert requests[0].headers["authorization"] == "Bearer vision-access-token"
    payload = json.loads(requests[0].content)
    assert payload["requests"][0]["features"] == [
        {"type": "DOCUMENT_TEXT_DETECTION"}
    ]


def test_pdf_ocr_reads_every_page_in_explicit_five_page_batches() -> None:
    requested_page_batches: list[list[int]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        pages = payload["requests"][0]["pages"]
        requested_page_batches.append(pages)
        page_responses = [
            {
                "fullTextAnnotation": {
                    "text": "" if page_number == 2 else f"{page_number}쪽 내용"
                }
            }
            for page_number in pages
        ]
        return httpx.Response(200, json={"responses": [{"responses": page_responses}]})

    client = GoogleVisionOcrClient(
        access_tokens=FixedAccessTokenProvider(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    pages = client.extract_pages(
        content=_blank_pdf(page_count=6),
        content_type="application/pdf",
    )

    assert requested_page_batches == [[1, 2, 3, 4, 5], [6]]
    assert pages == ["1쪽 내용", "", "3쪽 내용", "4쪽 내용", "5쪽 내용", "6쪽 내용"]


def test_vision_page_error_is_not_hidden_as_empty_ocr_text() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "responses": [
                    {
                        "error": {
                            "code": 7,
                            "message": "permission denied",
                        }
                    }
                ]
            },
        )

    client = GoogleVisionOcrClient(
        access_tokens=FixedAccessTokenProvider(),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    try:
        client.extract_pages(content=b"jpeg-content", content_type="image/jpeg")
    except DocumentParseError as error:
        assert error.code == "VISION_OCR_PAGE_FAILED"
    else:
        raise AssertionError("Cloud Vision page errors must fail document extraction")


def _blank_pdf(*, page_count: int) -> bytes:
    from io import BytesIO

    from pypdf import PdfWriter

    output = BytesIO()
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=100, height=100)
    writer.write(output)
    return output.getvalue()
