from __future__ import annotations

import base64
import json
import os
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fal_client
import pytest
import typer
from PIL import Image
from rich.console import Console
from typer.testing import CliRunner

from scripts import generate_character_profile_images as script


@pytest.fixture(autouse=True)
def project_root(tmp_path, monkeypatch):
    """Keep tests independent of the checkout's real .env and credentials."""
    root = tmp_path / "project"
    monkeypatch.setattr(
        script,
        "__file__",
        str(root / "scripts" / "generate_character_profile_images.py"),
    )
    return root


@pytest.fixture
def fal(monkeypatch):
    with BytesIO() as buffer:
        Image.new("RGB", (9, 16), "red").save(buffer, format="PNG")
        url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    submit = AsyncMock(
        side_effect=[SimpleNamespace(request_id=f"r{i}") for i in range(4)]
    )
    result = AsyncMock(return_value={"images": [{"url": url}]})
    monkeypatch.setattr(fal_client, "submit_async", submit)
    monkeypatch.setattr(fal_client, "result_async", result)
    return SimpleNamespace(submit=submit, result=result)


def make_profile(character_id="c1", **updates):
    return {
        "id": character_id,
        "book_id": "book",
        "character_name": "[red]A character[/red]",
        "character_alias": [],
        "character_profile_description": "A synthetic character.",
        "headshot_visual_prompt": f"Head and shoulders of {character_id}.",
        "full_body_visual_prompt": f"Entire figure of {character_id}.",
        **updates,
    }


@pytest.mark.parametrize("existing_key", [None, "shell-test-key"])
async def test_loads_project_dotenv_before_submission_without_overriding_environment(
    tmp_path, monkeypatch, fal, project_root, existing_key
):
    project_root.mkdir()
    (project_root / ".env").write_text("FAL_KEY=dotenv-test-key\n")
    monkeypatch.setenv("FAL_KEY", existing_key or "")
    if existing_key is None:
        monkeypatch.delenv("FAL_KEY", raising=False)
    run = tmp_path / "run"
    run.mkdir()
    (run / "character-profiles.json").write_text(json.dumps([make_profile()]))
    monkeypatch.chdir(run)

    async def submit(*args, **kwargs):
        assert os.environ["FAL_KEY"] == (existing_key or "dotenv-test-key")
        return SimpleNamespace(request_id=f"r{fal.submit.await_count}")

    fal.submit.side_effect = submit
    await script.main(run)
    assert fal.submit.await_count == 1


async def test_generates_only_headshots_for_every_profile(
    tmp_path, monkeypatch, capsys, fal
):
    run = tmp_path / "run"
    run.mkdir()
    profiles = [make_profile(), make_profile("../c2", full_body_visual_prompt=" ")]
    source = json.dumps(profiles)
    (run / "character-profiles.json").write_text(source)
    response = fal.result.return_value

    async def get_result(*args, **kwargs):
        assert fal.submit.await_count == 2
        return response

    fal.result.side_effect = get_result
    monkeypatch.chdir(tmp_path)

    await script.main(run.relative_to(tmp_path))

    assert [
        call.kwargs["arguments"]["prompt"] for call in fal.submit.await_args_list
    ] == [profile["headshot_visual_prompt"] for profile in profiles]
    assert all(
        {
            key: call.kwargs["arguments"][key]
            for key in ("resolution", "aspect_ratio", "enable_prompt_expansion")
        }
        == {
            "resolution": "1k",
            "aspect_ratio": "9:16",
            "enable_prompt_expansion": False,
        }
        for call in fal.submit.await_args_list
    )
    output_directory = run / "character-profile-images"
    assert {path.name for path in output_directory.iterdir()} == {
        "c1-headshot.jpg",
        "..%2Fc2-headshot.jpg",
    }
    for path in output_directory.iterdir():
        with Image.open(path) as image:
            assert image.format == "JPEG"
            assert image.size == (9, 16)
    assert (run / "character-profiles.json").read_text() == source
    output = capsys.readouterr()
    assert "Generated 2 images for 2 characters" in output.out
    assert "2/2 images" in output.err
    assert "Character images complete" in output.err


@pytest.mark.parametrize(
    "content",
    [
        "invalid JSON",
        json.dumps([{}]),
        json.dumps([make_profile(), make_profile()]),
        json.dumps([make_profile("")]),
        json.dumps([make_profile(), make_profile("c2", headshot_visual_prompt=" ")]),
    ],
)
async def test_invalid_profiles_rejected_before_generation(tmp_path, content, fal):
    (tmp_path / "character-profiles.json").write_text(content)
    with pytest.raises(typer.BadParameter):
        await script.main(tmp_path)
    fal.submit.assert_not_awaited()
    assert not (tmp_path / "character-profile-images").exists()


@pytest.mark.parametrize("missing_directory", [False, True])
def test_cli_reports_missing_input(tmp_path, missing_directory):
    run = tmp_path / "missing" if missing_directory else tmp_path
    result = CliRunner().invoke(script.app, [str(run)])
    assert result.exit_code == 2
    assert (
        "Expected an ebook run directory"
        if missing_directory
        else "character-profiles.json"
    ) in result.output


async def test_empty_profiles_complete_without_api_calls(tmp_path, capsys, fal):
    (tmp_path / "character-profiles.json").write_text("[]")
    await script.main(tmp_path)
    fal.submit.assert_not_awaited()
    fal.result.assert_not_awaited()
    output = capsys.readouterr()
    assert "Generated 0 images" in output.out
    assert "0/0 images" in output.err


async def test_generation_failure_keeps_completed_images_and_progress(
    tmp_path, monkeypatch, fal, capsys
):
    (tmp_path / "character-profiles.json").write_text(
        json.dumps([make_profile(), make_profile("c2")])
    )
    # The first request fails immediately; the successful result must still save.
    fal.result.side_effect = [
        RuntimeError("generation failed"),
        fal.result.return_value,
    ]
    progress = script.create_progress(Console(file=StringIO()))
    monkeypatch.setattr(script, "create_progress", lambda console: progress)

    with pytest.raises(typer.Exit) as error:
        await script.main(tmp_path)

    assert error.value.exit_code == 1
    assert fal.result.await_count == 2
    assert progress.tasks[0].completed == 1
    assert progress.tasks[0].total == 2
    assert {p.name for p in (tmp_path / "character-profile-images").iterdir()} == {
        "c2-headshot.jpg"
    }
    assert "generation failed" in capsys.readouterr().err


def test_failed_save_preserves_previous_image(tmp_path, monkeypatch):
    path = tmp_path / "c1-headshot.jpg"
    path.write_bytes(b"previous image")

    def fail(stream, **kwargs):
        stream.write(b"partial image")
        raise OSError("save failed")

    with Image.new("RGB", (9, 16)) as image:
        monkeypatch.setattr(image, "save", fail)
        with pytest.raises(OSError, match="save failed"):
            script.save_image(path, image)

    assert path.read_bytes() == b"previous image"
    assert list(tmp_path.iterdir()) == [path]


async def test_partial_submission_failure_still_collects_accepted_jobs(
    tmp_path, fal, capsys
):
    (tmp_path / "character-profiles.json").write_text(
        json.dumps([make_profile(), make_profile("c2")])
    )
    fal.submit.side_effect = [
        SimpleNamespace(request_id="accepted"),
        RuntimeError("submission failed"),
    ]
    with pytest.raises(typer.Exit) as error:
        await script.main(tmp_path)
    assert error.value.exit_code == 1
    fal.result.assert_awaited_once_with(script.Flux3T2Ihandler.endpoint, "accepted")
    assert (tmp_path / "character-profile-images" / "c1-headshot.jpg").is_file()
    assert "submission failed" in capsys.readouterr().err


async def test_submission_failure_does_not_claim_images_were_saved(
    tmp_path, fal, capsys
):
    (tmp_path / "character-profiles.json").write_text(json.dumps([make_profile()]))
    fal.submit.side_effect = RuntimeError("No credentials found")
    with pytest.raises(typer.Exit):
        await script.main(tmp_path)
    output = capsys.readouterr()
    assert "No images were saved." in output.out
    assert "Saved to" not in output.out
    fal.result.assert_not_awaited()


async def test_save_failure_still_saves_other_results(tmp_path, fal, monkeypatch):
    (tmp_path / "character-profiles.json").write_text(
        json.dumps([make_profile(), make_profile("c2")])
    )
    save = script.save_image

    def save_image(path, image):
        if path.name == "c1-headshot.jpg":
            raise OSError("disk error")
        save(path, image)

    monkeypatch.setattr(script, "save_image", save_image)
    with pytest.raises(typer.Exit) as error:
        await script.main(tmp_path)
    assert error.value.exit_code == 1
    assert (tmp_path / "character-profile-images" / "c2-headshot.jpg").is_file()


async def test_output_symlink_rejected_before_generation(tmp_path, fal):
    run = tmp_path / "run"
    run.mkdir()
    (run / "character-profiles.json").write_text(json.dumps([make_profile()]))
    (run / "character-profile-images").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(typer.BadParameter, match="symbolic link"):
        await script.main(run)
    fal.submit.assert_not_awaited()
