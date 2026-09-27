# fix(local-runtime): size a GGUF tensor whose ggml type the table never heard of

Fixes #119192.

## The bug

`read_gguf_header()` sizes a GGUF from its tensor table with `_GGML_TYPE_SIZES` and **raises** on
any type id the table does not carry. Ternary 1.58-bit quants use ggml types **142** and **143**, so
for `Ternary-Bonsai-2-27B-{PQ2_0,PTQ1_0}.gguf` the chain is:

```
read_gguf_header()  → ValueError: unknown ggml tensor type 142
preset_for_model()  → None                       (hermes_cli/local_runtime/presets.py:89)
generate_presets()  → "wrote 0 preset sections"
router              → GET /v1/models → {"data":[]}
every turn          → 400: model 'Ternary-Bonsai-2-27B-PQ2_0' not found
```

The preset INI is the router's only per-model arg carrier (`--models-preset`), so the model is not
merely refused — it is **invisible**, and the server has nothing to autoload. Reproduced on current
`main` (`424d4bdbd4`); `gguf.py` is byte-identical to the version in the report.

## Why not just add rows 142/143

The issue proposes two table rows and explicitly asks that the block-byte figures be verified
against the `ggml-common.h` of the build that defines them. They can't be, from here, and guessing
is not a neutral act:

- **142/143 are a distribution's own extension**, numbered well outside upstream ggml's range
  (upstream is still in the 30s). Their geometry lives only in that fork's header, and pinning a
  vendor-private id in core invites a collision if upstream ever reaches those numbers.
- **The number feeds the VRAM planner directly.** `tensor_bytes` → `ModelProfile.weights_bytes` →
  `plan_launch()`. An underestimate buys a context window the card cannot hold; on Windows/WDDM that
  over-commit is not refused, it is silently paged (see `admitted_residency_count`'s docstring).
- **It does not fix the class.** The table has now been narrower than the runtime twice — MXFP4/39
  (#116681), then 142/143 — and it will be again.

## The fix

The file already carries the exact answer. Every GGUF tensor info stores its **data offset**, which
`read_gguf_header` was discarding (`f.read(8)  # offset`). For a type the table does not know, the
tensor's size is the gap to the next offset — or, for the last one, to the end of the data section,
derived from `general.alignment` and `st_size`. Padding included, exact, and it cannot go stale as
new quant types appear.

```python
def _offset_spans(offsets: list[int], data_bytes: int) -> dict[int, int]:
    ordered = sorted(set(offsets))
    next_offset = dict(zip(ordered, ordered[1:]))
    return {off: max(next_offset.get(off, data_bytes) - off, 0) for off in ordered}
```

Two properties kept deliberately:

- **Known types keep their table arithmetic**, untouched. No staged model changes size, so no
  existing plan moves. The span walk only runs for a type that is actually missing.
- **The refusal stays where it is load-bearing.** When there is no data section to measure — a
  truncated download — an unknown type still raises rather than price a weight at zero. Failing
  closed there is the whole point: a free-looking weight is the outcome that OOMs the card.

`_GGML_TYPE_SIZES` is left alone; no unverified vendor constants ship. A 142/143 row can still be
added later, from the real header, as a pure refinement.

## Validation

**E2E, real imports, real `generate_presets()`, temp `HERMES_HOME`** — a synthesized GGUF matching
the reported shape (`general.architecture: qwen35`, `full_attention_interval`, 49 tensors of type
142/143, 7.18 GiB data section) on an 8 GB budget:

| | current `main` | with this change |
|---|---|---|
| `read_gguf_header` | `ValueError: unknown ggml tensor type 142` | `tensor_bytes = 7707033600` (= the data section, byte for byte) |
| `generate_presets` | `wrote 0 preset sections` | `wrote 2 preset sections` |
| plan | — | `ctx-size = 65536`, `override-tensor = blk\.\d+\.ffn_.*\.weight=CPU`, spilled |
| sampling | — | `temp = 0.7` picked up from `general.sampling.*` |

**Unit** — three behaviour contracts in `tests/hermes_cli/test_local_runtime_gguf.py`, the first two
proven red on base (`ValueError: unknown ggml tensor type 142` / `143`):

- `test_reader_sizes_a_type_the_table_never_heard_of` — type 142 is sized from the data section.
- `test_unknown_type_is_measured_to_the_next_tensor_not_to_the_file_end` — an unknown tensor between
  two known ones owns only the gap to the next offset (32 B), not the rest of the file (96 B); the
  known rows keep their exact table size.
- `test_unknown_type_with_no_data_section_still_refuses` — fail-closed on a truncated file.

The existing MXFP4 contract is untouched and still passes.

**Regression surface** — `scripts/run_tests.sh` over the 28 `tests/hermes_cli/test_local*.py`,
`test_context_policy.py`, `test_catalog_variants.py`, `test_boot_preset_staleness.py`,
`test_lmstudio_context_policy.py`: 235 passed, 0 failed. `ruff check` clean.

## Scope / not in this PR

- **The engine still has to support the type to load it.** Bundled `b10964` does not carry
  `PQ2_0`/`PTQ1_0`, so on that build the model now reaches llama-server and fails there with a real
  load error instead of vanishing silently. The issue's own framing is that "the block is in
  `gguf.py`, not in the runtime" — on a ternary-capable build, Hermes now plans the file.
- **Surfacing the skip reason in the picker** is #117326 and is left to it.
- **The session pinning a refused model in `state.db.sessions.model_config`** is the report's own
  "secondary, may deserve its own issue" — a separate defect in the resume path, not touched here.

`grep -rn "ggml.\?type"` confirms `gguf.py` is the only tensor-type table in the tree, so there is no
sibling call path to fix. No docs reference it.
