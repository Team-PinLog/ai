import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "seal-runtime-secrets.yml"
LEGACY_WORKFLOW = ROOT / ".github" / "workflows" / "seal-ai-secrets.yml"
AI_CI = ROOT / ".github" / "workflows" / "ai-ci.yml"
ACTION = (
    "Team-PinLog/infra/.github/actions/sealedsecret-infra-pr@"
    "84458bf35e341b79e91ce21a3667e9d3f7454068"
)
SECRET_KEYS = {
    "GMS_API_KEY",
    "GMS_BASE_URL",
    "INTERNAL_SHARED_SECRET",
    "PINLOG_INFRA_SECRET_PR_TOKEN",
}


def load_workflow() -> tuple[dict, str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def test_canonical_workflow_is_manual_only_and_legacy_workflow_is_removed():
    workflow, text = load_workflow()

    assert not LEGACY_WORKFLOW.exists()
    assert list(WORKFLOW.parent.glob("*seal*secret*.yml")) == [WORKFLOW]
    assert workflow["on"] == {"workflow_dispatch": ""}
    assert "repository_dispatch" not in text


def test_sealing_job_has_the_minimal_environment_and_permission_contract():
    workflow, _ = load_workflow()

    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    assert len(workflow["jobs"]) == 1
    job = next(iter(workflow["jobs"].values()))
    assert job["environment"] == "pinlog-secrets-dev"


def test_checkout_and_shared_action_are_immutable_and_revision_bound():
    workflow, text = load_workflow()
    job = next(iter(workflow["jobs"].values()))
    checkout = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    action = next(step for step in job["steps"] if step.get("uses") == ACTION)

    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
    assert checkout["with"] == {
        "persist-credentials": "false",
        "ref": "${{ github.sha }}",
    }
    assert action["with"] == {
        "policy": "ai-dev",
        "revision": "${{ github.sha }}",
    }
    assert "Team-PinLog/infra/.github/actions/sealedsecret-infra-pr@" in text


def test_shared_action_receives_only_the_exact_environment_secret_keys():
    workflow, text = load_workflow()
    job = next(iter(workflow["jobs"].values()))
    action = next(step for step in job["steps"] if step.get("uses") == ACTION)

    assert set(action["env"]) == SECRET_KEYS
    assert action["env"] == {key: f"${{{{ secrets.{key} }}}}" for key in SECRET_KEYS}
    for key in SECRET_KEYS:
        assert text.count(f"${{{{ secrets.{key} }}}}") == 1
    assert all(
        "${{ secrets." not in str(step)
        for step in job["steps"]
        if step is not action
    )
    assert "placeholder" not in text.lower()
    assert "base64" not in text.lower()


def test_general_ci_does_not_read_runtime_or_handoff_secrets():
    text = AI_CI.read_text(encoding="utf-8")

    for key in SECRET_KEYS:
        assert f"secrets.{key}" not in text
