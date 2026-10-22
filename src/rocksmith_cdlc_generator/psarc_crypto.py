"""Minimal AES-256-CFB-128 primitives for decrypting Rocksmith PSARC TOCs.

Rocksmith PSARC files store their table of contents AES-256-CFB encrypted
(feedback size 128 bits, zero IV, zero padding) with a fixed key. This module
implements just enough AES (256-bit key expansion + block encryption) to run
CFB-128 decryption, keeping the reader dependency-free.

CFB decryption only requires the AES *encrypt* direction, which simplifies the
implementation considerably.
"""

from __future__ import annotations

PSARC_KEY = bytes.fromhex(
    "C53DB23870A1A2F71CAE64061FDD0E1157309DC85204D4C5BFDF25090DF2572C"
)
_ZERO_IV = bytes(16)

_SBOX = [
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
]

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D]


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a & 0xFF


def _expand_key(key: bytes) -> list[list[int]]:
    """Expand a 32-byte (256-bit) key into 60 round words (Nr=14)."""
    nk = 8
    nr = 14
    words: list[list[int]] = [[key[4 * i + j] for j in range(4)] for i in range(nk)]

    for i in range(nk, 4 * (nr + 1)):
        temp = words[i - 1][:]
        if i % nk == 0:
            temp = temp[1:] + temp[:1]  # RotWord
            temp = [_SBOX[b] for b in temp]  # SubWord
            temp[0] ^= _RCON[i // nk - 1]
        elif i % nk == 4:
            temp = [_SBOX[b] for b in temp]  # SubWord
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    return words


def _add_round_key(state: list[int], round_keys: list[list[int]]) -> None:
    for c in range(4):
        for r in range(4):
            state[r + 4 * c] ^= round_keys[c][r]


def _shift_rows(state: list[int]) -> None:
    # state is column-major: state[r + 4*c]
    out = [0] * 16
    for r in range(4):
        for c in range(4):
            out[r + 4 * c] = state[r + 4 * ((c + r) % 4)]
    state[:] = out


def _mix_columns(state: list[int]) -> None:
    for c in range(4):
        a0 = state[0 + 4 * c]
        a1 = state[1 + 4 * c]
        a2 = state[2 + 4 * c]
        a3 = state[3 + 4 * c]
        state[0 + 4 * c] = _xtime(a0) ^ (_xtime(a1) ^ a1) ^ a2 ^ a3
        state[1 + 4 * c] = a0 ^ _xtime(a1) ^ (_xtime(a2) ^ a2) ^ a3
        state[2 + 4 * c] = a0 ^ a1 ^ _xtime(a2) ^ (_xtime(a3) ^ a3)
        state[3 + 4 * c] = (_xtime(a0) ^ a0) ^ a1 ^ a2 ^ _xtime(a3)


def _encrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    nr = 14
    state = list(block)
    _add_round_key(state, round_keys[0:4])
    for r in range(1, nr):
        state = [_SBOX[b] for b in state]
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, round_keys[4 * r : 4 * r + 4])
    state = [_SBOX[b] for b in state]
    _shift_rows(state)
    _add_round_key(state, round_keys[4 * nr : 4 * nr + 4])
    return bytes(state)


def cfb128_decrypt(ciphertext: bytes, key: bytes, iv: bytes = _ZERO_IV) -> bytes:
    """AES-CFB-128 decryption (feedback/segment size 128 bits)."""
    if len(key) != 32:
        raise ValueError("PSARC TOC decryption requires a 32-byte key")
    round_keys = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(ciphertext), 16):
        keystream = _encrypt_block(prev, round_keys)
        chunk = ciphertext[i : i + 16]
        for j, byte in enumerate(chunk):
            out.append(byte ^ keystream[j])
        prev = chunk + b"\x00" * (16 - len(chunk))
    return bytes(out)


def decrypt_toc(ciphertext: bytes, key: bytes = PSARC_KEY) -> bytes:
    """Decrypt a PSARC table-of-contents region (header-relative bytes)."""
    return cfb128_decrypt(ciphertext, key)


def cfb128_encrypt(plaintext: bytes, key: bytes = PSARC_KEY, iv: bytes = _ZERO_IV) -> bytes:
    """AES-CFB-128 encryption. The keystream is generated from the previous
    *ciphertext* block (the CFB feedback path), so this is not identical to
    ``cfb128_decrypt`` even though both XOR with the same keystream sequence."""
    if len(key) != 32:
        raise ValueError("PSARC TOC encryption requires a 32-byte key")
    round_keys = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(plaintext), 16):
        keystream = _encrypt_block(prev, round_keys)
        chunk = plaintext[i : i + 16]
        cipher = bytes(b ^ keystream[j] for j, b in enumerate(chunk))
        out += cipher
        prev = cipher + b"\x00" * (16 - len(cipher))
    return bytes(out)
