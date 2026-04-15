from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Protocol

from .types import DecryptError


@dataclass(frozen=True)
class Ciphertext:
    ciphertext: bytes
    nonce: bytes
    tag: bytes
    encryption_context: dict[str, str]


@dataclass(frozen=True)
class DataKey:
    plaintext: bytes
    encrypted: Ciphertext


class KMSClientProtocol(Protocol):
    def encrypt(self, plaintext: bytes, encryption_context: dict[str, str]) -> Ciphertext: ...
    def decrypt(self, ciphertext: Ciphertext, encryption_context: dict[str, str]) -> bytes: ...
    def generate_data_key(self, context: dict[str, str]) -> DataKey: ...


class NoneKMSClient:
    """No-op KMS client. Raises on any operation. Use when encryption is not needed."""

    def encrypt(self, plaintext: bytes, encryption_context: dict[str, str]) -> Ciphertext:
        raise NotImplementedError("KMS provider is 'none' — envelope encryption disabled")

    def decrypt(self, ciphertext: Ciphertext, encryption_context: dict[str, str]) -> bytes:
        raise NotImplementedError("KMS provider is 'none' — envelope encryption disabled")

    def generate_data_key(self, context: dict[str, str]) -> DataKey:
        raise NotImplementedError("KMS provider is 'none' — envelope encryption disabled")


class LocalAESClient:
    """AES-GCM envelope encryption for dev. Key from LOCAL_KMS_KEY_HEX env var."""

    def __init__(self, key_hex: str | None = None) -> None:
        hex_key = key_hex or os.environ.get("LOCAL_KMS_KEY_HEX", "")
        if not hex_key:
            hex_key = secrets.token_hex(32)
        self._key = bytes.fromhex(hex_key)

    def encrypt(self, plaintext: bytes, encryption_context: dict[str, str]) -> Ciphertext:
        if not encryption_context:
            raise ValueError("encryption_context must not be empty")
        # TODO: Implement AES-GCM encryption with AAD from encryption_context
        #   - Use cryptography.hazmat.primitives.ciphers.aead.AESGCM
        #   - Serialize encryption_context deterministically as AAD
        #   - Return Ciphertext with nonce, ciphertext, tag, and context
        raise NotImplementedError("TODO: implement AES-GCM encrypt")

    def decrypt(self, ciphertext: Ciphertext, encryption_context: dict[str, str]) -> bytes:
        if encryption_context != ciphertext.encryption_context:
            raise DecryptError("encryption context mismatch")
        # TODO: Implement AES-GCM decryption
        #   - Reconstruct AAD from encryption_context
        #   - Verify tag and decrypt
        raise NotImplementedError("TODO: implement AES-GCM decrypt")

    def generate_data_key(self, context: dict[str, str]) -> DataKey:
        if not context:
            raise ValueError("encryption_context must not be empty")
        # TODO: Generate a random 256-bit data key, encrypt it with the master key
        raise NotImplementedError("TODO: implement data key generation")


# TODO: Implement AwsKMSClient
#   - Use boto3 kms client
#   - encrypt/decrypt via KMS Encrypt/Decrypt APIs with EncryptionContext
#   - generate_data_key via KMS GenerateDataKey
#   - Lazy-import boto3
