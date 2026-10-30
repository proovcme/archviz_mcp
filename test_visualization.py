"""Behavioral gates using synthetic asset hashes; no customer assets or paid renders."""
import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from visualization import Series, check_series, context_hash


def snapshot(name, char="a"):
    return {"asset_id": name, "sha256": char * 64}


def brief():
    model, source = snapshot("model"), snapshot("view", "b")
    return {
        "project_id": "synthetic-test", "goal": "photographic", "execution": "generative",
        "model": model, "geometry_sources": [source],
        "cameras": [{"camera_id": "entrance", "sha256": "c" * 64, "description": "Human-eye approach"}],
        "material_brief": "Pale plaster and matte roof", "lighting_brief": "Soft daylight",
        "allowed_changes": ["Material appearance and environment"],
        "pilot_candidate_id": "pilot", "selected_candidate_ids": ["pilot"],
        "candidates": [{"candidate_id": "pilot", "image": snapshot("pilot-image", "d"),
                        "model": model, "camera_id": "entrance", "camera_sha256": "c" * 64,
                        "geometry_sources": [source]}],
    }


def review_all(data):
    series = Series.model_validate(data)
    for candidate, payload in zip(series.candidates, data["candidates"]):
        payload["review"] = {
            "context_sha256": context_hash(candidate, series),
            "geometry": {"status": "pass", "observations": ["Compared openings and canopy to source."]},
            "appearance": {"status": "pass", "observations": ["Inspected light coherence and material response."]},
            "series_consistency": {"status": "pass", "observations": ["Compared shared facade and palette."]},
        }
    return data


def accept(data, author="user", decision="accepted", scope="appearance"):
    data.setdefault("feedback", []).append({"image": copy.deepcopy(data["candidates"][0]["image"]),
        "author": author, "decision": decision, "scope": scope, "quote": "This is the approved photographic reference."})
    return data


def evaluate(data):
    return check_series(Series.model_validate(data))


def unpack(response):
    return response.structuredContent or json.loads(response.content[0].text)


class ReviewGateChecks(unittest.TestCase):
    def test_missing_pilot_is_not_ready(self):
        data = brief(); data["candidates"] = []; data["selected_candidate_ids"] = []
        result = evaluate(data)
        self.assertFalse(result["can_expand_series"])
        self.assertFalse(result["can_release_selection"])
        self.assertIn("pilot_missing", result["pilot"]["blockers"])

    def test_agent_cannot_accept_pilot_for_user(self):
        result = evaluate(accept(review_all(brief()), author="agent"))
        self.assertIn("pilot_needs_user_acceptance", result["pilot"]["blockers"])

    def test_explicit_user_appearance_acceptance_and_both_checks_release_single(self):
        result = evaluate(accept(review_all(brief())))
        self.assertTrue(result["can_expand_series"])
        self.assertTrue(result["can_release_selection"])

    def test_illustrative_goal_does_not_impose_photographic_user_pause(self):
        data = brief(); data["goal"] = "illustrative"; review_all(data)
        self.assertTrue(evaluate(data)["can_expand_series"])

    def test_camera_requiring_unseen_source_is_blocked(self):
        data = brief(); data["cameras"][0]["authority"] = "source_required"; accept(review_all(data))
        self.assertIn("camera_source_required", evaluate(data)["pilot"]["blockers"])

    def test_appearance_acceptance_does_not_pass_geometry(self):
        data = accept(review_all(brief()))
        data["candidates"][0]["review"]["geometry"] = {"status": "fail", "observations": ["Two extra porch steps."]}
        result = evaluate(data)
        self.assertFalse(result["can_expand_series"])
        self.assertIn("geometry_fail", result["pilot"]["blockers"])

    def test_unreviewed_appearance_does_not_pass(self):
        data = accept(review_all(brief()))
        data["candidates"][0]["review"]["appearance"] = {"status": "not_reviewed"}
        self.assertFalse(evaluate(data)["can_release_selection"])

    def test_overwriting_same_image_name_invalidates_review_and_approval(self):
        data = accept(review_all(brief()))
        data["candidates"][0]["image"]["sha256"] = "e" * 64
        result = evaluate(data)
        self.assertIn("visual_review_stale", result["pilot"]["blockers"])
        self.assertFalse(result["pilot"]["reported_user_acceptance"])

    def test_model_camera_and_source_revisions_require_fresh_review(self):
        for field in ("model", "camera", "source"):
            with self.subTest(field=field):
                data = accept(review_all(brief()))
                if field == "model": data["model"]["sha256"] = "e" * 64
                elif field == "camera": data["cameras"][0]["sha256"] = "e" * 64
                else: data["geometry_sources"][0]["sha256"] = "e" * 64
                self.assertFalse(evaluate(data)["can_release_selection"])

    def test_changed_brief_invalidates_review(self):
        for field in ("material_brief", "lighting_brief", "goal"):
            with self.subTest(field=field):
                data = accept(review_all(brief()))
                data[field] = "illustrative" if field == "goal" else "Changed target"
                self.assertIn("visual_review_stale", evaluate(data)["pilot"]["blockers"])

    def test_updating_candidate_inputs_cannot_reuse_old_review(self):
        data = accept(review_all(brief()))
        data["model"]["sha256"] = data["candidates"][0]["model"]["sha256"] = "e" * 64
        self.assertIn("visual_review_stale", evaluate(data)["pilot"]["blockers"])

    def test_user_rejection_cannot_be_overruled_by_agent(self):
        data = accept(review_all(brief()), decision="rejected")
        accept(data, author="agent")
        self.assertIn("user_rejected_appearance", evaluate(data)["pilot"]["blockers"])

    def test_latest_user_feedback_can_reconsider_same_image(self):
        data = accept(review_all(brief()), decision="rejected"); accept(data)
        self.assertTrue(evaluate(data)["can_release_selection"])

    def test_explicit_user_waiver_only_waives_pause(self):
        data = review_all(brief()); data["pilot_waiver"] = {"author": "user", "quote": "Produce the series without waiting."}
        self.assertTrue(evaluate(data)["can_expand_series"])
        data["candidates"][0]["review"] = None
        self.assertFalse(evaluate(data)["can_expand_series"])
        data["pilot_waiver"]["author"] = "agent"
        with self.assertRaises(ValidationError): Series.model_validate(data)

    def test_rejected_alternative_can_stay_in_history_but_not_selection(self):
        data = accept(review_all(brief())); alternative = copy.deepcopy(data["candidates"][0])
        alternative.update(candidate_id="discarded", image=snapshot("discarded-image", "e"), review=None)
        data["candidates"].append(alternative)
        self.assertTrue(evaluate(data)["can_release_selection"])
        data["selected_candidate_ids"] = ["discarded"]
        self.assertFalse(evaluate(data)["can_release_selection"])

    def test_series_requires_camera_coverage_and_cross_view_review(self):
        data = brief(); data["cameras"].append({"camera_id": "garden", "sha256": "e" * 64, "description": "Garden view"})
        accept(review_all(data))
        self.assertIn("camera_coverage_incomplete", evaluate(data)["release_blockers"])
        garden = copy.deepcopy(data["candidates"][0]); garden.update(candidate_id="garden", camera_id="garden", camera_sha256="e" * 64, image=snapshot("garden-image", "f"))
        data["candidates"].append(garden); data["selected_candidate_ids"].append("garden"); review_all(data)
        data["candidates"][1]["review"]["series_consistency"] = {"status": "fail", "observations": ["Roof material differs from pilot."]}
        result = evaluate(data)
        self.assertTrue(result["can_expand_series"])
        self.assertFalse(result["can_release_selection"])
        review_all(data)
        self.assertTrue(evaluate(data)["can_release_selection"])

    def test_strict_geometry_requires_recorded_shared_scene(self):
        data = brief(); data["strict_geometry"] = True; accept(review_all(data))
        self.assertIn("strict_geometry_requires_scene_render", evaluate(data)["pilot"]["blockers"])
        data["execution"] = "scene_render"; data["scene_lock"] = {"scene": snapshot("scene"), "materials": snapshot("materials"), "lighting": snapshot("lighting")}
        review_all(data)
        self.assertFalse(evaluate(data)["can_expand_series"])
        data["candidates"][0]["scene_lock"] = copy.deepcopy(data["scene_lock"]); review_all(data)
        self.assertTrue(evaluate(data)["can_expand_series"])
        data["scene_lock"]["materials"]["sha256"] = "b" * 64
        self.assertFalse(evaluate(data)["can_release_selection"])

    def test_blank_observations_and_duplicate_ids_rejected(self):
        data = review_all(brief()); data["candidates"][0]["review"]["appearance"]["observations"] = [" "]
        with self.assertRaises(ValidationError): Series.model_validate(data)
        data = brief(); data["candidates"].append(copy.deepcopy(data["candidates"][0]))
        with self.assertRaises(ValidationError): Series.model_validate(data)


class ProtocolChecks(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_workflow_and_user_gate(self):
        params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).parent / "server.py")])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                names = {tool.name for tool in tools}
                self.assertTrue({"reference_sources", "search_references", "read_reference", "view_reference_image",
                                 "visualization_workflow", "check_visualization_series"} <= names)
                workflow = await session.call_tool("visualization_workflow", {})
                self.assertFalse(workflow.isError)
                payload = unpack(workflow)
                self.assertFalse(payload["capabilities"]["renders_images"])
                data = brief()
                initial = await session.call_tool("check_visualization_series", {"series": data})
                self.assertFalse(initial.isError, initial.content)
                context = unpack(initial)["candidates"][0]["review_context_sha256"]
                review_all(data)
                self.assertEqual(data["candidates"][0]["review"]["context_sha256"], context)
                unapproved = await session.call_tool("check_visualization_series", {"series": data})
                self.assertFalse(unpack(unapproved)["can_expand_series"])
                accepted = await session.call_tool("check_visualization_series", {"series": accept(data)})
                self.assertFalse(accepted.isError)
                self.assertTrue(unpack(accepted)["can_release_selection"])


if __name__ == "__main__":
    unittest.main()
