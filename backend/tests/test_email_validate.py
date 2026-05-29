"""Email validation (NeverBounce wrapper)."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from app.services import email_validate
from app.services.email_validate import (
    ValidationStatus,
    confidence_label_for,
    validate_email,
)


def test_no_key_returns_unknown():
    with patch.object(email_validate.settings, "neverbounce_api_key", ""):
        assert validate_email("a@b.com") == ValidationStatus.UNKNOWN


def test_malformed_email_is_invalid():
    with patch.object(email_validate.settings, "neverbounce_api_key", "k"):
        assert validate_email("not-an-email") == ValidationStatus.INVALID
        assert validate_email("") == ValidationStatus.INVALID


def _resp(json_body, status=200):
    return httpx.Response(status, json=json_body)


def test_valid_result_maps_to_valid():
    with patch.object(email_validate.settings, "neverbounce_api_key", "k"), \
         patch("httpx.Client") as MockClient:
        inst = MockClient.return_value.__enter__.return_value
        inst.get.return_value = _resp({"status": "success", "result": "valid"})
        assert validate_email("a@b.com") == ValidationStatus.VALID


def test_catchall_maps_to_catchall():
    with patch.object(email_validate.settings, "neverbounce_api_key", "k"), \
         patch("httpx.Client") as MockClient:
        inst = MockClient.return_value.__enter__.return_value
        inst.get.return_value = _resp({"status": "success", "result": "catchall"})
        assert validate_email("a@b.com") == ValidationStatus.CATCHALL


def test_disposable_maps_to_invalid():
    with patch.object(email_validate.settings, "neverbounce_api_key", "k"), \
         patch("httpx.Client") as MockClient:
        inst = MockClient.return_value.__enter__.return_value
        inst.get.return_value = _resp({"status": "success", "result": "disposable"})
        assert validate_email("a@b.com") == ValidationStatus.INVALID


def test_api_error_status_returns_unknown():
    with patch.object(email_validate.settings, "neverbounce_api_key", "k"), \
         patch("httpx.Client") as MockClient:
        inst = MockClient.return_value.__enter__.return_value
        inst.get.return_value = _resp({"status": "auth_failure", "message": "bad key"})
        assert validate_email("a@b.com") == ValidationStatus.UNKNOWN


def test_http_error_returns_unknown():
    with patch.object(email_validate.settings, "neverbounce_api_key", "k"), \
         patch("httpx.Client") as MockClient:
        inst = MockClient.return_value.__enter__.return_value
        inst.get.side_effect = httpx.ConnectError("down")
        assert validate_email("a@b.com") == ValidationStatus.UNKNOWN


def test_http_4xx_returns_unknown():
    with patch.object(email_validate.settings, "neverbounce_api_key", "k"), \
         patch("httpx.Client") as MockClient:
        inst = MockClient.return_value.__enter__.return_value
        inst.get.return_value = _resp({"result": "valid"}, status=429)
        assert validate_email("a@b.com") == ValidationStatus.UNKNOWN


def test_confidence_label_mapping():
    assert confidence_label_for(ValidationStatus.VALID) == "verified"
    assert confidence_label_for(ValidationStatus.CATCHALL) == "unverifiable_catchall"
    assert confidence_label_for(ValidationStatus.UNKNOWN) == "pattern_guess"
