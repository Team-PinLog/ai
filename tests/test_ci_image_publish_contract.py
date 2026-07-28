import re
from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "ai-ci.yml"
SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def load_workflow() -> tuple[dict, str]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def test_external_actions_are_commit_pinned_and_checkout_drops_credentials():
    workflow, _ = load_workflow()
    uses = []
    checkout_steps = []
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" not in step:
                continue
            uses.append(step["uses"])
            if step["uses"].startswith("actions/checkout@"):
                checkout_steps.append(step)

    assert uses
    assert all(SHA_PIN.fullmatch(action) for action in uses)
    assert checkout_steps
    assert all(
        step.get("with", {}).get("persist-credentials") == "false"
        for step in checkout_steps
    )


def test_untrusted_pr_title_is_passed_via_environment_not_interpolated_in_shell():
    workflow, _ = load_workflow()
    title_step = next(
        step
        for step in workflow["jobs"]["check"]["steps"]
        if step.get("name") == "Validate PR title"
    )
    assert title_step["env"]["PR_TITLE"] == "${{ github.event.pull_request.title }}"
    assert "github.event.pull_request.title" not in title_step["run"]
    assert 'printf \'%s\\n\' "$PR_TITLE"' in title_step["run"]


def test_publish_is_main_push_only_after_successful_ci_with_job_scoped_write():
    workflow, _ = load_workflow()
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == (
        "${{ github.event_name == 'pull_request' }}"
    )

    publish = workflow["jobs"]["image-publish"]
    assert publish["needs"] == "check"
    assert "github.event_name == 'push'" in publish["if"]
    assert "github.ref == 'refs/heads/main'" in publish["if"]
    assert publish["permissions"] == {"contents": "read", "packages": "write"}

    for name, job in workflow["jobs"].items():
        if name != "image-publish":
            assert "packages" not in job.get("permissions", {})


def test_publish_checks_out_and_pushes_only_the_exact_40_char_success_sha():
    workflow, text = load_workflow()
    publish = workflow["jobs"]["image-publish"]
    checkout = next(
        step
        for step in publish["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.sha }}"

    build = next(step for step in publish["steps"] if step.get("id") == "publish")
    preflight = next(
        step
        for step in publish["steps"]
        if step.get("name") == "Refuse to overwrite existing commit tag"
    )
    script = preflight["run"]
    assert preflight["env"]["GITHUB_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert "https://ghcr.io/token" in script
    assert "https://ghcr.io/v2/team-pinlog/ai/manifests/${SOURCE_SHA}" in script
    assert "Authorization: Bearer ${registry_token}" in script
    assert 'case "$http_status" in' in script
    assert "404)" in script
    assert "200)" in script
    assert "curl_status" in script
    assert "manifest unknown|not found" not in script
    assert "exit 1" in script

    assert build["with"]["push"] == "true"
    assert build["with"]["tags"] == "ghcr.io/team-pinlog/ai:${{ github.sha }}"
    assert re.search(r"\[\[ \"\$SOURCE_SHA\" =~ \^\[0-9a-f\]\{40\}\$ \]\]", text)

    forbidden = (":latest", ":main", "docker.io/", "PERSONAL_ACCESS_TOKEN", "GHCR_TOKEN")
    assert not any(value in text for value in forbidden)


def test_publish_uses_github_token_and_fails_closed_on_registry_digest_verification():
    workflow, text = load_workflow()
    publish = workflow["jobs"]["image-publish"]
    login = next(
        step
        for step in publish["steps"]
        if step.get("uses", "").startswith("docker/login-action@")
    )
    assert login["with"]["password"] == "${{ secrets.GITHUB_TOKEN }}"

    verify = next(
        step
        for step in publish["steps"]
        if step.get("name") == "Verify published image digest"
    )
    script = verify["run"]
    assert "set -euo pipefail" in script
    assert '[[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]' in script
    assert 'docker buildx imagetools inspect "${IMAGE}:${SOURCE_SHA}"' in script
    assert "--format '{{.Manifest.Digest}}'" in script
    assert 'test "$REGISTRY_DIGEST" = "$IMAGE_DIGEST"' in script
    assert 'ghcr.io/team-pinlog/ai:${SOURCE_SHA}@${IMAGE_DIGEST}' in script
    assert "docker buildx imagetools inspect" in script
    assert "Published digest:" in script

    # Login and push must remain unreachable from pull_request jobs.
    assert text.count("docker/login-action@") == 1
    assert text.count("push: true") == 1


def test_successful_publish_emits_exact_digest_bound_provenance_artifact():
    workflow, _ = load_workflow()
    steps = workflow["jobs"]["image-publish"]["steps"]
    names = [step.get("name") for step in steps]
    create_index = names.index("Create verified image provenance")
    verify_index = names.index("Verify published image digest")
    assert create_index > verify_index

    create = steps[create_index]
    assert create["env"] == {
        "IMAGE_DIGEST": "${{ steps.publish.outputs.digest }}"
    }
    script = create["run"]
    for field in (
        "source_repository",
        "source_sha",
        "image_repository",
        "digest",
    ):
        assert field in script
    assert '[[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]' in script
    assert "provenance.json" in script
    assert "jq -e" in script

    upload = next(
        step
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["uses"] == (
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    )
    assert upload["with"] == {
        "name": "ai-image-provenance",
        "path": "provenance.json",
        "if-no-files-found": "error",
        "retention-days": "7",
    }
