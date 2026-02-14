from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from src.core.wiki_client import WikiClient, WikiClientError


@pytest.fixture
def client() -> WikiClient:
    return WikiClient("https://wiki.test.com", "test-api-key", timeout=10)


def _mock_response(data: dict) -> MagicMock:
    """Create a mock urllib response with JSON data."""
    body = json.dumps(data).encode("utf-8")
    mock = MagicMock()
    mock.read.return_value = body
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


class TestGraphqlRequest:
    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_basic_request(self, mock_urlopen: MagicMock, client: WikiClient) -> None:
        mock_urlopen.return_value = _mock_response({"data": {"__typename": "Query"}})
        result = client.graphql_request("{ __typename }")
        assert result == {"data": {"__typename": "Query"}}

    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_passes_timeout(self, mock_urlopen: MagicMock, client: WikiClient) -> None:
        mock_urlopen.return_value = _mock_response({"data": {}})
        client.graphql_request("{ __typename }")
        _, kwargs = mock_urlopen.call_args
        assert kwargs["timeout"] == 10

    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_http_error_raises(self, mock_urlopen: MagicMock, client: WikiClient) -> None:
        mock_urlopen.side_effect = HTTPError(
            "https://wiki.test.com/graphql", 401, "Unauthorized",
            {}, BytesIO(b"Auth failed"),
        )
        with pytest.raises(WikiClientError) as exc_info:
            client.graphql_request("{ __typename }")
        assert exc_info.value.status_code == 401
        assert "401" in str(exc_info.value)

    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_url_error_raises(self, mock_urlopen: MagicMock, client: WikiClient) -> None:
        mock_urlopen.side_effect = URLError("DNS lookup failed")
        with pytest.raises(WikiClientError) as exc_info:
            client.graphql_request("{ __typename }")
        assert "Connection error" in str(exc_info.value)
        assert exc_info.value.status_code is None

    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_sends_auth_header(self, mock_urlopen: MagicMock, client: WikiClient) -> None:
        mock_urlopen.return_value = _mock_response({"data": {}})
        client.graphql_request("{ __typename }")
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer test-api-key"

    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_sends_variables(self, mock_urlopen: MagicMock, client: WikiClient) -> None:
        mock_urlopen.return_value = _mock_response({"data": {}})
        client.graphql_request("query ($x: Int!) { }", {"x": 42})
        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data)
        assert payload["variables"] == {"x": 42}


class TestTestConnection:
    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_returns_true_on_success(self, mock_urlopen: MagicMock, client: WikiClient) -> None:
        mock_urlopen.return_value = _mock_response({"data": {"__typename": "Query"}})
        assert client.test_connection() is True

    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_raises_on_failure(self, mock_urlopen: MagicMock, client: WikiClient) -> None:
        mock_urlopen.side_effect = URLError("Connection refused")
        with pytest.raises(WikiClientError):
            client.test_connection()


class TestCheckPageExists:
    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_found(self, mock_urlopen: MagicMock, client: WikiClient) -> None:
        mock_urlopen.return_value = _mock_response({
            "data": {"pages": {"singleByPath": {"id": 42, "title": "Test"}}}
        })
        result = client.check_page_exists("Docs/test", "en")
        assert result == {"id": 42, "title": "Test"}

    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_not_found(self, mock_urlopen: MagicMock, client: WikiClient) -> None:
        mock_urlopen.return_value = _mock_response({
            "data": {"pages": {"singleByPath": None}}
        })
        result = client.check_page_exists("Docs/missing", "en")
        assert result is None


class TestTimeoutParameter:
    def test_default_timeout(self) -> None:
        c = WikiClient("https://wiki.test.com", "key")
        assert c.timeout == 30

    def test_custom_timeout(self) -> None:
        c = WikiClient("https://wiki.test.com", "key", timeout=60)
        assert c.timeout == 60

    @patch("src.core.wiki_client.urllib.request.urlopen")
    def test_timeout_propagates_to_urlopen(self, mock_urlopen: MagicMock) -> None:
        c = WikiClient("https://wiki.test.com", "key", timeout=45)
        mock_urlopen.return_value = _mock_response({"data": {}})
        c.graphql_request("{ __typename }")
        _, kwargs = mock_urlopen.call_args
        assert kwargs["timeout"] == 45
