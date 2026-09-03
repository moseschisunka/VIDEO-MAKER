"""Contract tests for the iLearnZed creator-studio foundation."""

from pathlib import Path

from lib.content_templates import list_content_templates, load_content_template
from lib.creator_profile import load_creator_profile
from lib.pipeline_loader import load_pipeline
from tools.publishers.thumbnail_package import ThumbnailPackageBuilder
from tools.publishers.thumbnail_renderer import ThumbnailPreviewRenderer


TEMPLATE_NAMES = {
    "ilearnzed-exam-lesson",
    "ilearnzed-concept-explainer",
    "ilearnzed-study-methods",
    "ilearnzed-teacher-onboarding",
    "ilearnzed-product-demonstration",
    "ilearnzed-shorts",
}


def _variants():
    return [
        {
            "id": "a",
            "on_image_text": "SOLVE THIS",
            "focal_subject": "teacher and question",
            "visual_proof": "worked example",
            "logo_treatment": "padded bottom-right",
            "mobile_readability": "large text",
        },
        {
            "id": "b",
            "on_image_text": "STOP LOSING MARKS",
            "focal_subject": "wrong and right steps",
            "visual_proof": "annotated answer",
            "logo_treatment": "padded bottom-right",
            "mobile_readability": "large text",
        },
        {
            "id": "c",
            "on_image_text": "EXAM METHOD",
            "focal_subject": "final solution",
            "visual_proof": "practice question",
            "logo_treatment": "padded bottom-right",
            "mobile_readability": "large text",
        },
    ]


def test_all_ilearnzed_templates_load_and_reference_valid_pipelines():
    assert set(list_content_templates()) == TEMPLATE_NAMES
    for name in TEMPLATE_NAMES:
        template = load_content_template(name)
        load_pipeline(template["production"]["default_pipeline"])
        assert "thumbnail_package" in template["production"]["required_artifacts"]
        assert template["consent"]["evidence_required_before_publish"] is True


def test_thumbnail_package_builder_includes_profile_context():
    result = ThumbnailPackageBuilder().execute(
        {
            "video_id": "demo-maths",
            "template": "ilearnzed-exam-lesson",
            "viewer_promise": "Solve this exam question clearly",
            "title_options": [
                "How to Solve This Question",
                "The Step That Saves Marks",
                "GCE Maths Made Clear",
                "Stop Losing Marks Here",
                "A Better Way to Solve Sets",
            ],
            "thumbnail_variants": _variants(),
            "recommended_pair": {
                "title": "The Step That Saves Marks",
                "thumbnail_variant_id": "a",
                "viewer_expectation": "A clear method for the exact question",
            },
        }
    )

    assert result.success, result.error
    package = result.data["thumbnail_package"]
    assert package["metadata"]["profile_id"] == load_creator_profile()["id"]
    assert package["metadata"]["cta_destination"] == "https://ilearnzed.org"
    assert package["metadata"]["logo_asset"] == "assets/brand/ilearnzed-logo.jpg"


def test_thumbnail_package_builder_rejects_unlisted_recommendation():
    result = ThumbnailPackageBuilder().execute(
        {
            "video_id": "demo-maths",
            "template": "ilearnzed-exam-lesson",
            "viewer_promise": "Solve this exam question clearly",
            "title_options": ["A", "B", "C", "D", "E"],
            "thumbnail_variants": _variants(),
            "recommended_pair": {
                "title": "Not in the title options",
                "thumbnail_variant_id": "a",
                "viewer_expectation": "A clear method",
            },
        }
    )

    assert not result.success
    assert "title_options" in (result.error or "")


def test_thumbnail_preview_renderer_attaches_project_local_previews(tmp_path):
    package_result = ThumbnailPackageBuilder().execute(
        {
            "video_id": "renderer-demo",
            "template": "ilearnzed-exam-lesson",
            "viewer_promise": "Solve this exam question clearly",
            "title_options": ["A", "B", "C", "D", "E"],
            "thumbnail_variants": _variants(),
            "recommended_pair": {
                "title": "A",
                "thumbnail_variant_id": "a",
                "viewer_expectation": "A clear method",
            },
        }
    )
    assert package_result.success, package_result.error

    result = ThumbnailPreviewRenderer().execute(
        {
            "project_dir": str(tmp_path),
            "thumbnail_package": package_result.data["thumbnail_package"],
        }
    )

    assert result.success, result.error
    package = result.data["thumbnail_package"]
    assert len(result.data["preview_paths"]) == 3
    assert all(item["preview_path"].startswith("assets/images/thumbnails/") for item in package["thumbnail_variants"])
    assert all(path.endswith(".svg") for path in result.data["preview_paths"])
    assert (tmp_path / "artifacts" / "thumbnail_package.json").is_file()
    assert all(Path(path).is_file() for path in result.data["preview_paths"])
    assert "data:image/jpeg;base64," in Path(result.data["preview_paths"][0]).read_text(encoding="utf-8")
