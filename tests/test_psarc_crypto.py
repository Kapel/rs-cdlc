from __future__ import annotations

from rocksmith_cdlc_generator.psarc_crypto import _encrypt_block, _expand_key, cfb128_decrypt, cfb128_encrypt


def test_aes256_known_answer() -> None:
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    plaintext = bytes.fromhex("00112233445566778899aabbccddeeff")
    expected = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
    assert _encrypt_block(plaintext, _expand_key(key)) == expected


def test_cfb_round_trip() -> None:
    key = bytes.fromhex("C53DB23870A1A2F71CAE64061FDD0E1157309DC85204D4C5BFDF25090DF2572C")
    iv = bytes(16)
    plaintext = bytes(range(1, 65))  # 64 bytes, multiple of 16
    ciphertext = cfb128_encrypt(plaintext, key, iv)
    assert ciphertext != plaintext
    assert cfb128_decrypt(ciphertext, key, iv) == plaintext


def test_cfb_partial_block_round_trip() -> None:
    key = bytes.fromhex("C53DB23870A1A2F71CAE64061FDD0E1157309DC85204D4C5BFDF25090DF2572C")
    iv = bytes(16)
    for length in (1, 5, 15, 16, 17, 31, 33, 47):
        plaintext = bytes((i * 7 + 3) % 256 for i in range(length))
        assert cfb128_decrypt(cfb128_encrypt(plaintext, key, iv), key, iv) == plaintext
