from chatterbox.models.s3gen import decoder as _decoder


def add_optional_chunk_mask_nosync(xs, masks, use_dynamic_chunk, use_dynamic_left_chunk, decoding_chunk_size,
                                   static_chunk_size, num_decoding_left_chunks, enable_full_context=True):
    assert not use_dynamic_chunk and static_chunk_size == 0
    return masks | (masks.sum(dim=-1, keepdim=True) == 0)


def apply():
    _decoder.add_optional_chunk_mask = add_optional_chunk_mask_nosync
