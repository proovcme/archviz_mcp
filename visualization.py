"""Stateless review contracts. No image scoring, rendering, file access or persistence."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = str


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Snapshot(Contract):
    """The host hashes actual bytes; a path/name alone cannot identify a revision."""
    asset_id: str = Field(min_length=1, max_length=120)
    sha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")


class Camera(Contract):
    camera_id: str = Field(min_length=1, max_length=120)
    sha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    description: str = Field(min_length=1, max_length=1200)
    authority: Literal["locked", "selectable", "source_required"] = "selectable"


class SceneLock(Contract):
    scene: Snapshot
    materials: Snapshot
    lighting: Snapshot


class AppearanceReference(Snapshot):
    origin: Literal["photograph", "generated", "bim_export", "unknown"]
    use_for: list[Literal["camera", "lighting", "materials", "photographic_treatment"]] = Field(min_length=1, max_length=4)


class Check(Contract):
    status: Literal["not_reviewed", "pass", "fail"] = "not_reviewed"
    observations: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def require_observations(self):
        if self.status != "not_reviewed" and not any(s.strip() for s in self.observations):
            raise ValueError("A pass/fail requires concrete observations from inspected images.")
        return self


class Review(Contract):
    """Host-supplied visual judgments, bound to the exact image AND its inputs."""
    context_sha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    consistency_context_sha256: Digest | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    geometry: Check = Field(default_factory=Check)
    appearance: Check = Field(default_factory=Check)
    series_consistency: Check = Field(default_factory=Check)


class Candidate(Contract):
    candidate_id: str = Field(min_length=1, max_length=120)
    image: Snapshot
    model: Snapshot
    camera_id: str = Field(min_length=1, max_length=120)
    camera_sha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    geometry_sources: list[Snapshot] = Field(min_length=1, max_length=20)
    scene_lock: SceneLock | None = None
    review: Review | None = None


class Feedback(Contract):
    image: Snapshot
    author: Literal["user", "agent"]
    decision: Literal["accepted", "rejected"]
    scope: Literal["geometry", "appearance", "overall"]
    quote: str = Field(min_length=1, max_length=2000)


class PilotWaiver(Contract):
    """Only an explicit user instruction can waive the pause, not visual review."""
    author: Literal["user"]
    quote: str = Field(min_length=1, max_length=2000)


class Series(Contract):
    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1, max_length=120)
    goal: Literal["photographic", "illustrative"]
    execution: Literal["generative", "scene_render"]
    strict_geometry: bool = False
    model: Snapshot
    geometry_sources: list[Snapshot] = Field(min_length=1, max_length=20)
    appearance_references: list[AppearanceReference] = Field(default_factory=list, max_length=20)
    scene_lock: SceneLock | None = None
    cameras: list[Camera] = Field(min_length=1, max_length=30)
    material_brief: str = Field(min_length=1, max_length=2000)
    lighting_brief: str = Field(min_length=1, max_length=2000)
    allowed_changes: list[str] = Field(min_length=1, max_length=30)
    pilot_candidate_id: str | None = None
    pilot_waiver: PilotWaiver | None = None
    candidates: list[Candidate] = Field(default_factory=list, max_length=100)
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=30)
    # Append in conversation order. Feedback never becomes a global style preference.
    feedback: list[Feedback] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def unique_identifiers(self):
        groups = {
            "camera_id": [c.camera_id for c in self.cameras],
            "candidate_id": [c.candidate_id for c in self.candidates],
            "selected_candidate_ids": self.selected_candidate_ids,
            "geometry source asset_id": [s.asset_id for s in self.geometry_sources],
        }
        for label, values in groups.items():
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate {label}.")
        return self


def context_hash(candidate: Candidate, series: Series) -> str:
    """Changing image, camera, model, geometry source or scene invalidates review."""
    value = candidate.model_dump(exclude={"review", "candidate_id"})
    value["geometry_sources"] = sorted(value["geometry_sources"], key=lambda s: (s["asset_id"], s["sha256"]))
    value["brief"] = series.model_dump(include={"project_id", "goal", "execution", "strict_geometry", "material_brief",
                                               "lighting_brief", "allowed_changes", "appearance_references",
                                               "scene_lock"})
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def consistency_context_hash(series: Series) -> str:
    """Bind cross-view review to the selected revisions and pilot, independent of order."""
    by_id = {c.candidate_id: c for c in series.candidates}
    ids = set(series.selected_candidate_ids)
    if series.pilot_candidate_id is not None:
        ids.add(series.pilot_candidate_id)
    value = {
        "selected_candidate_ids": sorted(series.selected_candidate_ids),
        "pilot_candidate_id": series.pilot_candidate_id,
        "contexts": {cid: context_hash(by_id[cid], series) if cid in by_id else None
                     for cid in sorted(ids)},
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def user_decision(series: Series, image: Snapshot, scope: str) -> str | None:
    """Only explicit reported USER feedback on these exact bytes counts."""
    matching = [f for f in series.feedback if f.author == "user" and f.image == image
                and f.scope in (scope, "overall")]
    return matching[-1].decision if matching else None


def candidate_blockers(series: Series, candidate: Candidate, consistency: bool = False) -> list[str]:
    blockers = []
    cameras = {c.camera_id: c for c in series.cameras}
    camera = cameras.get(candidate.camera_id)
    if candidate.model != series.model:
        blockers.append("stale_model")
    if camera is None:
        blockers.append("unknown_camera")
    elif camera.sha256 != candidate.camera_sha256:
        blockers.append("stale_camera")
    if camera and camera.authority == "source_required":
        blockers.append("camera_source_required")
    if not all(s in series.geometry_sources for s in candidate.geometry_sources):
        blockers.append("stale_or_unknown_geometry_source")
    if len({s.asset_id for s in candidate.geometry_sources}) != len(candidate.geometry_sources):
        blockers.append("duplicate_geometry_source")
    if series.execution == "scene_render":
        if series.scene_lock is None or candidate.scene_lock != series.scene_lock:
            blockers.append("missing_or_changed_scene_lock")
    elif series.strict_geometry:
        blockers.append("strict_geometry_requires_scene_render")
    for scope in ("geometry", "appearance"):
        if user_decision(series, candidate.image, scope) == "rejected":
            blockers.append(f"user_rejected_{scope}")
    review = candidate.review
    if review is None:
        blockers.append("visual_review_missing")
    elif review.context_sha256 != context_hash(candidate, series):
        blockers.append("visual_review_stale")
    else:
        if consistency and review.consistency_context_sha256 != consistency_context_hash(series):
            blockers.append("series_consistency_stale")
        for axis in ("geometry", "appearance") + (("series_consistency",) if consistency else ()):
            status = getattr(review, axis).status
            if status != "pass":
                blockers.append(f"{axis}_{status}")
    return blockers


def check_series(series: Series) -> dict:
    """Evaluate recorded evidence completeness; cannot see pixels or authenticate quotes."""
    by_id = {c.candidate_id: c for c in series.candidates}
    pilot = by_id.get(series.pilot_candidate_id)
    pilot_blockers = candidate_blockers(series, pilot) if pilot else ["pilot_missing"]
    pilot_blockers.extend(f"camera_source_required:{c.camera_id}" for c in series.cameras
                          if c.authority == "source_required")
    accepted = bool(pilot and user_decision(series, pilot.image, "appearance") == "accepted")
    waived = series.pilot_waiver is not None
    if not pilot_blockers and series.goal == "photographic" and not (accepted or waived):
        pilot_blockers.append("pilot_needs_user_acceptance")
    can_expand = not pilot_blockers
    rows = []
    release_blockers = list(pilot_blockers)
    selected = []
    for candidate_id in series.selected_candidate_ids:
        candidate = by_id.get(candidate_id)
        if candidate is None:
            release_blockers.append(f"unknown_selected_candidate:{candidate_id}")
        else:
            selected.append(candidate)
    if not selected:
        release_blockers.append("selection_missing")
    required = {c.camera_id for c in series.cameras}
    covered = [c.camera_id for c in selected]
    if set(covered) != required:
        release_blockers.append("camera_coverage_incomplete")
    if len(set(covered)) != len(covered):
        release_blockers.append("multiple_selected_candidates_for_camera")
    for candidate in series.candidates:
        blockers = candidate_blockers(series, candidate, consistency=len(selected) > 1)
        rows.append({"candidate_id": candidate.candidate_id, "image": candidate.image.model_dump(),
                     "review_context_sha256": context_hash(candidate, series), "blockers": blockers,
                     "selected": candidate.candidate_id in series.selected_candidate_ids})
        if candidate in selected:
            release_blockers.extend(f"{candidate.candidate_id}:{b}" for b in blockers)
    return {
        "project_id": series.project_id, "can_expand_series": can_expand,
        "can_release_selection": not release_blockers,
        "pilot": {"candidate_id": series.pilot_candidate_id,
                  "reported_user_acceptance": accepted, "reported_user_waiver": waived,
                  "blockers": pilot_blockers},
        "release_blockers": release_blockers, "candidates": rows,
        "consistency_context_sha256": consistency_context_hash(series),
        "assessment_boundary": "Validates host-supplied records, not photographic quality, file bytes, "
                               "actual renderer execution or authenticity of user quotes. "
                               "No numeric realism score. Inspect pixels and verify feedback in conversation.",
        "storage": "No files read or written. Keep this project manifest with the host, outside the source repository.",
    }


def workflow_contract() -> dict:
    return {
        "schema_version": 1,
        "capabilities": {"reference_research": ["officenext"], "review_contract": True,
                         "renders_images": False, "controls_bim_or_dcc": False,
                         "visual_scoring": False, "persistent_storage": False},
        "reference_roles": ["geometry", "photographic_appearance", "lighting", "camera"],
        "sequence": ["Record project brief and versioned model/cameras/reference roles.",
                     "Create one pilot with the available host renderer or image tool.",
                     "Inspect geometry and appearance separately; record observations and exact input hash.",
                     "Record the user's explicit decision on this exact image, or their explicit waiver of the pause.",
                     "Expand only after the pilot gate; review every selected view and series continuity.",
                     "Release only the checked selection; retain rejections and limitations in the project manifest."],
        "mode_selection": {"generative": "Conceptual images; exact geometry/series consistency is not guaranteed.",
                           "scene_render": "Host renders a shared scene with locked material/light revisions and cameras. "
                                           "Required for strict geometric consistency; verify backend availability first."},
        "review_axes": {"geometry": ["openings", "levels", "roof and canopies", "supports", "stairs", "accessories"],
                        "photographic_appearance": ["camera projection and scale", "light coherence",
                                                   "material response", "reflections", "contact with surroundings",
                                                   "synthetic detail and optical artifacts"],
                        "series_consistency": ["same building", "same material palette", "compatible surroundings/light"]},
        "feedback_policy": "User and agent judgments are distinct. Record exact user words, scope, and image hash. "
                           "A style approval does not pass geometry review. Never infer approval from silence, "
                           "a previous version, matching colors, or successful image generation.",
        "schema": Series.model_json_schema(),
    }
