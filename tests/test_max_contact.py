"""Tests for MAX contact parsing and hash verification."""

import hashlib
import hmac

from adapters.max.client import (
    compute_contact_hash,
    normalize_vcf_info,
    parse_phone_from_vcf,
    verify_contact_hash,
)

VCF_ESCAPED = (
    "BEGIN:VCARD\\r\\nVERSION:3.0\\r\\nPRODID:ez-vcard 0.10.3\\r\\n"
    "TEL;TYPE=cell:79991234567\\r\\nFN:Ivan Ivanov\\r\\nEND:VCARD\\r\\n"
)


def test_normalize_vcf_info_converts_escaped_newlines():
    normalized = normalize_vcf_info(VCF_ESCAPED)
    assert "\r\n" in normalized
    assert "\\r\\n" not in normalized


def test_parse_phone_from_vcf():
    phone = parse_phone_from_vcf(VCF_ESCAPED)
    assert phone == "79991234567"


def test_compute_and_verify_contact_hash():
    token = "test_bot_token"
    expected_hash = compute_contact_hash(token, VCF_ESCAPED)
    assert verify_contact_hash(token, VCF_ESCAPED, expected_hash)


def test_verify_contact_hash_rejects_invalid():
    token = "test_bot_token"
    assert not verify_contact_hash(token, VCF_ESCAPED, "invalid_hash")


def test_compute_contact_hash_matches_hmac_spec():
    token = "secret"
    vcf = "BEGIN:VCARD\\r\\nTEL;TYPE=cell:79990000000\\r\\nEND:VCARD\\r\\n"
    normalized = normalize_vcf_info(vcf)
    expected = hmac.new(
        token.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert compute_contact_hash(token, vcf) == expected
