# Copyright (c) 2024-2025 Alain Prasquier - Supervaize.com. All rights reserved.
#
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, you can obtain one at
# https://mozilla.org/MPL/2.0/.


from datetime import datetime

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.responses import JSONResponse

from supervaizer.common import decrypt_value, encrypt_value
from supervaizer.server_utils import ErrorResponse, ErrorType, create_error_response


def test_error_type_enum() -> None:
    assert ErrorType.JOB_NOT_FOUND.value == "job_not_found"
    assert ErrorType.JOB_ALREADY_EXISTS.value == "job_already_exists"
    assert ErrorType.AGENT_NOT_FOUND.value == "agent_not_found"
    assert ErrorType.INVALID_REQUEST.value == "invalid_request"
    assert ErrorType.INTERNAL_ERROR.value == "internal_error"
    assert ErrorType.INVALID_PARAMETERS.value == "invalid_parameters"


def test_error_response_model() -> None:
    error = ErrorResponse(
        error="Test Error",
        error_type=ErrorType.INVALID_REQUEST,
        detail="Test detail",
        status_code=400,
    )

    assert error.error == "Test Error"
    assert error.error_type == ErrorType.INVALID_REQUEST
    assert error.detail == "Test detail"
    assert error.status_code == 400
    assert isinstance(error.timestamp, datetime)


def test_create_error_response() -> None:
    response = create_error_response(
        error_type=ErrorType.JOB_NOT_FOUND, detail="Job 123 not found", status_code=404
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 404

    content = response.body.decode("utf-8")
    assert "Job Not Found" in content
    assert "Job 123 not found" in content
    assert "job_not_found" in content


def test_create_error_response_without_detail() -> None:
    response = create_error_response(
        error_type=ErrorType.INTERNAL_ERROR,
        detail="Internal server error",
        status_code=500,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 500

    content = response.body.decode("utf-8")
    assert "Internal Error" in content


def test_encrypt_decrypt() -> None:
    """Test encryption and decryption"""
    # Generate key pair
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    public_key = private_key.public_key()

    # Test string
    test_str = "test string"
    encrypted = encrypt_value(test_str, public_key)
    decrypted = decrypt_value(encrypted, private_key)
    assert isinstance(encrypted, str)
    assert isinstance(decrypted, str)
    assert decrypted == test_str

    # Test with raw bytes
    test_bytes = b"test bytes"
    encrypted_bytes = public_key.encrypt(
        test_bytes,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    decrypted_bytes = private_key.decrypt(
        encrypted_bytes,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    if isinstance(decrypted_bytes, memoryview):
        decrypted_bytes = bytes(decrypted_bytes)
    assert decrypted_bytes.decode() == test_bytes.decode()
    assert decrypted_bytes.decode() == test_bytes.decode()
