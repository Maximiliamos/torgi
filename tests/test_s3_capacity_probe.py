"""Unit tests for the direct multipart S3 capacity probe."""
import importlib.util
import urllib.parse
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "s3_capacity_probe",
    Path(__file__).resolve().parents[1] / "scripts" / "s3_capacity_probe.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Response:
    def __init__(self, status=200, content=b"", headers=None, text=""):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self.text = text


class FakeSession:
    def __init__(self, expected_bytes):
        self.expected_bytes = expected_bytes
        self.calls = []
        self.part = 0

    def request(self, method, url, data=None, headers=None, timeout=None):
        del headers, timeout
        size = len(data) if data is not None else 0
        self.calls.append((method, url, size))
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(url).query, keep_blank_values=True
        )
        if method == "POST" and "uploads" in query:
            return Response(
                content=(
                    b"<InitiateMultipartUploadResult>"
                    b"<UploadId>upload-1</UploadId>"
                    b"</InitiateMultipartUploadResult>"
                )
            )
        if method == "PUT":
            self.part += 1
            return Response(headers={"ETag": f'"etag-{self.part}"'})
        if method == "POST" and "uploadId" in query:
            return Response()
        if method == "HEAD":
            return Response(headers={"Content-Length": str(self.expected_bytes)})
        if method == "DELETE":
            return Response(status=204)
        raise AssertionError((method, url))


def test_canonical_query_is_sorted_and_encoded():
    assert module._canonical_query(
        {"uploadId": "a+/=", "partNumber": "2"}
    ) == "partNumber=2&uploadId=a%2B%2F%3D"


def test_capacity_probe_uploads_verifies_and_deletes():
    required = 10 * 1024 * 1024
    fake = FakeSession(required)
    result = module.probe_capacity(
        endpoint="https://s3.example.test",
        bucket="bucket",
        access_key="access",
        secret_key="secret",
        region="ru-1",
        required_bytes=required,
        part_size=5 * 1024 * 1024,
        session=fake,
    )
    assert result["allowed"] is True
    assert result["probe_object_deleted"] is True
    methods = [call[0] for call in fake.calls]
    assert methods == ["POST", "PUT", "PUT", "POST", "HEAD", "DELETE"]
