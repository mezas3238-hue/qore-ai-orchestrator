from __future__ import annotations

import io
import re
import zipfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

MAX_REVIEW_BYTES = 500_000
DEEPSEEK_MARKER_RE = re.compile(
    r"<!--\s*QORE-DEEPSEEK-REVIEW\s+package=(?P<package>\S+)\s+head=(?P<head>[0-9a-f]{40})\s*-->"
)


class VerdictClass(str, Enum):
    CLEAN = "CLEAN"
    FINDINGS = "FINDINGS"
    BLOCKED = "BLOCKED"
    MECHANICAL_FAILURE = "MECHANICAL_FAILURE"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class ReviewVerdictEvidence:
    reviewer: str
    package_id: str
    head_sha: str
    verdict: VerdictClass
    clean_marker: bool
    findings_marker: bool
    blocked_marker: bool
    mechanical_failure_marker: bool
    source: str
    marker_lines: tuple[str, ...]
    production_authority: bool = False

    def __post_init__(self) -> None:
        if self.production_authority:
            raise ValueError("production_authority must remain false")


def classify_review_text(text: str) -> tuple[VerdictClass, tuple[str, ...]]:
    if not isinstance(text, str) or not text.strip():
        return VerdictClass.AMBIGUOUS, ()
    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    upper = text.upper()
    clean = "HALLAZGOS: NINGUNO" in upper and "VALIDACIÓN OK" in upper
    not_ok = "VALIDACIÓN NO OK" in upper
    blocked = (
        "EVIDENCIA INSUFICIENTE" in upper
        or "VALIDACIÓN BLOQUEADA" in upper
        or "VALIDATION BLOCKED" in upper
        or "EVIDENCE INSUFFICIENT" in upper
    )
    mechanical = "MECHANICAL REVIEW FAILURE" in upper or "MECHANICAL FAILURE" in upper
    explicit_findings = (
        "HALLAZGOS:" in upper and "HALLAZGOS: NINGUNO" not in upper
    ) or not_ok
    markers = tuple(
        line
        for line in lines
        if (
            line.upper().startswith("HALLAZGOS")
            or "VALIDACIÓN" in line.upper()
            or "VALIDATION" in line.upper()
            or "EVIDENCIA INSUFICIENTE" in line.upper()
            or "EVIDENCE INSUFFICIENT" in line.upper()
            or "MECHANICAL" in line.upper()
        )
    )[-12:]
    classes = [clean, explicit_findings, blocked, mechanical]
    if sum(bool(value) for value in classes) != 1:
        return VerdictClass.AMBIGUOUS, markers
    if clean:
        return VerdictClass.CLEAN, markers
    if explicit_findings:
        return VerdictClass.FINDINGS, markers
    if blocked:
        return VerdictClass.BLOCKED, markers
    return VerdictClass.MECHANICAL_FAILURE, markers


def deepseek_review_from_pr_reviews(
    *,
    reviews: Sequence[Mapping[str, Any]],
    package_id: str,
    expected_head: str,
) -> ReviewVerdictEvidence:
    matches: list[str] = []
    for review in reviews:
        body = review.get("body")
        commit_id = review.get("commit_id")
        if not isinstance(body, str):
            continue
        marker = DEEPSEEK_MARKER_RE.search(body)
        if marker is None:
            continue
        if marker.group("package") != package_id or marker.group("head") != expected_head:
            continue
        if commit_id != expected_head:
            raise ValueError("DeepSeek review commit_id does not match frozen HEAD")
        matches.append(body)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one exact DeepSeek review; found {len(matches)}")
    verdict, marker_lines = classify_review_text(matches[0])
    return ReviewVerdictEvidence(
        reviewer="DEEPSEEK",
        package_id=package_id,
        head_sha=expected_head,
        verdict=verdict,
        clean_marker=verdict is VerdictClass.CLEAN,
        findings_marker=verdict is VerdictClass.FINDINGS,
        blocked_marker=verdict is VerdictClass.BLOCKED,
        mechanical_failure_marker=verdict is VerdictClass.MECHANICAL_FAILURE,
        source="qore-core pull request review",
        marker_lines=marker_lines,
    )


def _extract_text(archive_bytes: bytes, basename: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            matches = [name for name in archive.namelist() if Path(name).name == basename]
            if len(matches) != 1:
                raise ValueError(f"expected exactly one {basename}; found {len(matches)}")
            info = archive.getinfo(matches[0])
            if info.file_size > MAX_REVIEW_BYTES:
                raise ValueError(f"{basename} exceeds review evidence size bound")
            return archive.read(info).decode("utf-8")
    except (zipfile.BadZipFile, UnicodeError, KeyError) as exc:
        raise ValueError(f"could not decode {basename}") from exc


def claude_review_from_artifact(
    *,
    archive_bytes: bytes,
    package_id: str,
    expected_head: str,
) -> ReviewVerdictEvidence:
    text = _extract_text(archive_bytes, "claude-review.md")
    verdict, marker_lines = classify_review_text(text)
    return ReviewVerdictEvidence(
        reviewer="CLAUDE_CODE",
        package_id=package_id,
        head_sha=expected_head,
        verdict=verdict,
        clean_marker=verdict is VerdictClass.CLEAN,
        findings_marker=verdict is VerdictClass.FINDINGS,
        blocked_marker=verdict is VerdictClass.BLOCKED,
        mechanical_failure_marker=verdict is VerdictClass.MECHANICAL_FAILURE,
        source="exact Claude workflow artifact",
        marker_lines=marker_lines,
    )


def evidence_to_json(value: ReviewVerdictEvidence) -> dict[str, Any]:
    result = asdict(value)
    result["verdict"] = value.verdict.value
    return result
