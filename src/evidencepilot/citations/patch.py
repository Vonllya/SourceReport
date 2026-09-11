"""Offset-safe citation patching."""

from typing import Any

from ..models import AtomicClaim, ClaimPatchBatch
from .parser import CitationParserMixin


class CitationPatchMixin:
    @staticmethod
    def _apply_claim_patches(
        report: str, patch_batch: ClaimPatchBatch, problem_map: dict[str, AtomicClaim],
        source_map: dict[str, dict[str, Any]],
    ) -> tuple[str, int]:
        edits: list[tuple[int, int, str]] = []
        used_claims: set[str] = set()
        for patch in patch_batch.patches:
            claim = problem_map.get(patch.claim_id)
            if not claim or patch.claim_id in used_claims:
                continue
            if report[claim.start_offset:claim.end_offset] != claim.text:
                continue
            replacement = patch.replacement.strip() if patch.action == "replace" else ""
            replacement_ids = CitationParserMixin._inline_source_ids(replacement)
            if patch.action == "replace" and (
                not replacement_ids or any(source_id not in source_map for source_id in replacement_ids)
            ):
                continue
            if patch.action == "replace":
                replacement_claims = CitationParserMixin._atomic_claims(replacement)
                if ("\n" in replacement or "\r" in replacement
                        or len(replacement_claims) != 1
                        or replacement_claims[0].node_type != "paragraph"
                        or replacement_claims[0].text != replacement):
                    continue
            edits.append((claim.start_offset, claim.end_offset, replacement))
            used_claims.add(patch.claim_id)
        original = report
        for start_offset, end_offset, replacement in sorted(edits, reverse=True):
            report = report[:start_offset] + replacement + report[end_offset:]
        # Markup in a local edit must not hide or split untouched claims.
        parsed = {(c.start_offset, c.end_offset, c.text, c.node_type)
                  for c in CitationParserMixin._atomic_claims(report)}
        for claim in CitationParserMixin._atomic_claims(original):
            if any(start < claim.end_offset and end > claim.start_offset
                   for start, end, _ in edits):
                continue
            shift = sum(len(text) - (end - start) for start, end, text in edits
                        if end <= claim.start_offset)
            if (claim.start_offset + shift, claim.end_offset + shift,
                    claim.text, claim.node_type) not in parsed:
                return original, 0
        return report, len(edits)




def apply_claim_patches(report: str, patch_batch: ClaimPatchBatch, problem_map: dict[str, AtomicClaim], source_map: dict[str, dict[str, Any]]) -> tuple[str, int]:
    return CitationPatchMixin._apply_claim_patches(report, patch_batch, problem_map, source_map)
