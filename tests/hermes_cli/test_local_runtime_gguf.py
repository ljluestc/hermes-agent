"""Behavior contracts for the managed local runtime's GGUF reader.

The unknown-tensor-type contracts are a regression for #119192 (ternary 1.58-bit types 142/143
made a whole model invisible) and #116681 (MXFP4/39 before it): the type table is narrower than
the quant types in the wild, so an unrecognised type must be sized, not refused.
"""

from __future__ import annotations

import struct

import pytest

from hermes_cli.local_runtime.gguf import read_gguf_header

_ALIGNMENT = 32


def _write_gguf(path, tensors, *, data_len: int) -> None:
    """A minimal GGUF: ``tensors`` is [(name, dims, ggml_type, data_offset)], then ``data_len``
    bytes of tensor data starting at the first alignment boundary past the tensor table."""
    head = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), 0)
    for name, dims, ttype, offset in tensors:
        raw = name.encode()
        head += (struct.pack("<Q", len(raw)) + raw
                 + struct.pack("<I", len(dims)) + struct.pack(f"<{len(dims)}Q", *dims)
                 + struct.pack("<IQ", ttype, offset))
    path.write_bytes(head + b"\0" * (-len(head) % _ALIGNMENT) + b"\0" * data_len)


def test_reader_sizes_mxfp4_tensor_blocks(tmp_path):
    """MXFP4 stores 32 elements in one 17-byte block."""
    name = b"token_embd.weight"
    gguf = tmp_path / "gpt-oss.gguf"
    gguf.write_bytes(
        b"GGUF"
        + struct.pack("<IQQ", 3, 1, 0)
        + struct.pack("<Q", len(name))
        + name
        + struct.pack("<IQIQ", 1, 64, 39, 0)
    )

    header = read_gguf_header(gguf)

    assert header.tensor_bytes == 34
    assert header.embd_table_bytes == header.tensor_bytes


def test_reader_sizes_a_type_the_table_never_heard_of(tmp_path):
    """A ternary 1.58-bit type (142) is sized from the data section, not refused.

    Block geometry for a distribution's own quant types is not knowable from here; the file's
    layout is, and it is what the planner needs.
    """
    gguf = tmp_path / "Ternary-Bonsai-2-27B-PQ2_0.gguf"
    _write_gguf(gguf, [("token_embd.weight", (256,), 142, 0)], data_len=60)

    header = read_gguf_header(gguf)

    assert header.tensor_bytes == 60
    assert header.embd_table_bytes == 60


def test_unknown_type_is_measured_to_the_next_tensor_not_to_the_file_end(tmp_path):
    """Known types keep their table size; an unknown one owns only the gap to the next offset."""
    gguf = tmp_path / "mixed.gguf"
    _write_gguf(gguf, [
        ("blk.0.attn_q.weight", (64,), 0, 0),        # F32: 64 x 4 = 256
        ("token_embd.weight", (256,), 143, 256),     # unknown: 288 - 256 = 32
        ("blk.0.attn_k.weight", (32,), 1, 288),      # F16: 32 x 2 = 64
    ], data_len=352)

    header = read_gguf_header(gguf)

    assert header.embd_table_bytes == 32
    assert header.tensor_bytes == 256 + 32 + 64


def test_unknown_type_with_no_data_section_still_refuses(tmp_path):
    """Fail closed when there is nothing to measure: a truncated download must not price an
    unknown weight at zero and buy a context window the card cannot hold."""
    gguf = tmp_path / "truncated.gguf"
    _write_gguf(gguf, [("token_embd.weight", (256,), 142, 0)], data_len=0)

    with pytest.raises(ValueError, match="142"):
        read_gguf_header(gguf)
