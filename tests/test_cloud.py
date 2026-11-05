"""The clouds: what a CLI command needs, how a link keeps it inside one client's account,
and connecting a login without Ringkeeper holding it."""

import json
from typing import Any

import pytest

from ringkeeper import cloud_rules, destructive, remote
from ringkeeper.adapters import mcp
from ringkeeper.adapters.base import Target, ToolError, ToolKind
from ringkeeper.models import Env, InstanceType
from ringkeeper.services import cli_logins, remote_connections
from ringkeeper.vault import Secret


@pytest.mark.parametrize(
    ("cli", "command", "needs"),
    [
        ("aws", "aws ec2 describe-instances", cloud_rules.READ),
        ("aws", "aws s3 ls", cloud_rules.READ),
        ("aws", "aws iam list-users", cloud_rules.READ),
        ("aws", "aws ec2 get-console-output --instance-id i-1", cloud_rules.READ),
        ("aws", "aws ec2 modify-instance-attribute --instance-id i-1", cloud_rules.APPROVE),
        ("aws", "aws ec2 stop-instances --instance-ids i-1", cloud_rules.APPROVE),
        ("aws", "aws s3 cp a.txt s3://b/a.txt", cloud_rules.APPROVE),
        ("aws", "aws s3 sync . s3://b --delete", cloud_rules.CONFIRM),
        ("aws", "aws s3 rm s3://b/a.txt", cloud_rules.CONFIRM),
        ("aws", "aws ec2 terminate-instances --instance-ids i-1", cloud_rules.CONFIRM),
        ("aws", "aws ec2 run-instances --image-id ami-1", cloud_rules.CONFIRM),
        ("aws", "aws ec2 start-instances --instance-ids i-1", cloud_rules.CONFIRM),
        ("aws", "aws iam attach-role-policy --role-name r", cloud_rules.CONFIRM),
        ("aws", "aws iam update-user --user-name u", cloud_rules.CONFIRM),
        ("aws", "aws secretsmanager get-secret-value --secret-id s", cloud_rules.CONFIRM),
        ("aws", "aws ec2 get-password-data --instance-id i-1", cloud_rules.CONFIRM),
        ("aws", "aws ssm get-parameter --name n --with-decryption", cloud_rules.CONFIRM),
        ("aws", "aws sts get-session-token", cloud_rules.CONFIRM),
        ("aws", "aws sts assume-role --role-arn a", cloud_rules.CONFIRM),
        ("aws", "aws ec2 frobnicate-instances", cloud_rules.CONFIRM),
        ("aws", "aws ec2 describe-instances; aws iam delete-user", cloud_rules.CONFIRM),
        ("aws", "aws ec2 describe-instances | sh", cloud_rules.CONFIRM),
        ("aws", "", cloud_rules.CONFIRM),
        ("gcloud", ["compute", "instances", "list"], cloud_rules.READ),
        ("gcloud", ["compute", "instances", "describe", "web-1"], cloud_rules.READ),
        ("gcloud", ["compute", "instances", "stop", "web-1"], cloud_rules.APPROVE),
        ("gcloud", ["compute", "instances", "delete", "web-1"], cloud_rules.CONFIRM),
        ("gcloud", ["compute", "instances", "create", "list"], cloud_rules.CONFIRM),
        ("gcloud", ["compute", "instances", "update", "list"], cloud_rules.APPROVE),
        ("gcloud", ["projects", "add-iam-policy-binding", "p"], cloud_rules.CONFIRM),
        ("gcloud", ["secrets", "versions", "access", "latest"], cloud_rules.CONFIRM),
        ("gcloud", ["auth", "print-access-token"], cloud_rules.CONFIRM),
        ("oci", "compute instance list --compartment-id c", cloud_rules.READ),
        ("oci", "compute instance get --instance-id i", cloud_rules.READ),
        ("oci", "compute instance update --instance-id i", cloud_rules.APPROVE),
        ("oci", "compute instance launch --compartment-id c", cloud_rules.CONFIRM),
        ("oci", "compute instance terminate --instance-id i", cloud_rules.CONFIRM),
        ("oci", "compute instance action --action START", cloud_rules.CONFIRM),
        ("oci", "iam policy create --name p", cloud_rules.CONFIRM),
        ("oci", "iam user update --user-id u", cloud_rules.CONFIRM),
    ],
)
def test_what_a_command_needs(cli: str, command: Any, needs: str) -> None:
    assert cloud_rules.level(cli, command) == needs


@pytest.mark.parametrize(
    ("cli", "command"),
    [
        ("aws", "aws s3 ls --profile other-client"),
        ("aws", "aws s3 ls --profile=other-client"),
        ("aws", "aws s3 ls --endpoint-url https://evil.example"),
        ("gcloud", ["compute", "instances", "list", "--account=me@other.example"]),
        ("gcloud", ["compute", "instances", "list", "--impersonate-service-account=x"]),
        ("oci", "compute instance list --profile OTHER"),
        ("oci", "compute instance list --config-file /tmp/c"),
    ],
)
def test_a_flag_that_leaves_the_login_is_found(cli: str, command: Any) -> None:
    assert cloud_rules.escape(cli, command)


def connection(key: str) -> Target:
    return Target("c", Env.PROD, None, Secret("r", "{}"), {"remote": key})


def test_leaving_the_login_is_refused_before_anything_is_held() -> None:
    assert mcp.call_tool.classify is not None
    args = {"tool": "call_aws", "arguments": {"cli_command": "aws s3 ls --profile other"}}
    with pytest.raises(ToolError, match="--profile isn't allowed"):
        mcp.call_tool.classify(connection("aws"), args)


@pytest.mark.parametrize(
    ("key", "tool", "arguments", "kind"),
    [
        ("aws", "call_aws", {"cli_command": "aws ec2 describe-instances"}, ToolKind.READ),
        ("aws", "call_aws", {"cli_command": "aws ec2 stop-instances"}, ToolKind.WRITE),
        ("gcloud", "run_gcloud_command", {"args": ["compute", "disks", "list"]}, ToolKind.READ),
        ("oci", "run_oci_command", {"command": "iam user delete"}, ToolKind.WRITE),
    ],
)
def test_the_command_decides_whether_it_waits(
    key: str, tool: str, arguments: dict[str, Any], kind: ToolKind
) -> None:
    assert mcp.call_tool.classify is not None
    assert mcp.call_tool.classify(connection(key), {"tool": tool, "arguments": arguments}) is kind


def test_a_setting_cant_lower_a_command() -> None:
    config = {"remote": "aws", "tool_rules": {"call_aws": mcp.READ}}
    args = {"cli_command": "aws ec2 terminate-instances"}
    assert mcp.call_rule(config, "call_aws", args).level == mcp.CONFIRM
    config = {"remote": "aws", "tool_rules": {"call_aws": mcp.CONFIRM}}
    args = {"cli_command": "aws ec2 describe-instances"}
    assert mcp.call_rule(config, "call_aws", args).level == mcp.CONFIRM  # stricter stays


def test_the_reason_comes_from_the_command() -> None:
    args = {"tool": "call_aws", "arguments": {"cli_command": "aws ec2 terminate-instances"}}
    why = destructive.reason(InstanceType.MCP, "call_tool", args, {"remote": "aws"})
    assert why is not None


@pytest.mark.parametrize(
    ("tool", "needs"),
    [
        ("storage_account_get", mcp.READ),
        ("group_list", mcp.READ),
        ("storage_blob_upload", mcp.APPROVE),
        ("group_delete", mcp.CONFIRM),
        ("role_assignment_create", mcp.CONFIRM),
        ("keyvault_secret_get", mcp.CONFIRM),
    ],
)
def test_azures_tools_go_by_their_names(tool: str, needs: str) -> None:
    assert mcp.rule({"remote": "azure"}, tool).level == needs


def spec(key: str) -> remote.ScopeSpec:
    found = remote.spec_for("mcp", {"remote": key})
    assert found is not None
    return found


def test_a_link_adds_its_project_and_refuses_another() -> None:
    scope = {"project": "acme-prod"}
    done = remote.narrow(
        spec("gcloud"), scope, "run_gcloud_command", {"args": ["compute", "disks", "list"]}, None
    )
    assert done["args"] == ["compute", "disks", "list", "--project", "acme-prod"]
    with pytest.raises(remote.ScopeError, match="limited to google cloud project 'acme-prod'"):
        remote.narrow(
            spec("gcloud"), scope, "run_gcloud_command",
            {"args": ["compute", "disks", "list", "--project=globex-prod"]}, None,
        )  # fmt: skip


def test_a_compartment_is_added_to_a_list_and_required_on_anything_else() -> None:
    scope = {"compartment": "ocid1.compartment.oc1..acme"}
    tool = "run_oci_command"
    done = remote.narrow(spec("oci"), scope, tool, {"command": "compute instance list"}, None)
    assert done["command"].endswith("--compartment-id ocid1.compartment.oc1..acme")
    with pytest.raises(remote.ScopeError, match="doesn't name it"):
        remote.narrow(spec("oci"), scope, tool, {"command": "compute instance get"}, None)
    with pytest.raises(remote.ScopeError, match="limited to"):
        remote.narrow(spec("oci"), scope, tool, {"command": "compute instance list -c other"}, None)


def test_a_region_is_added_to_an_aws_command() -> None:
    done = remote.narrow(
        spec("aws"), {"region": "eu-west-1"}, "call_aws", {"cli_command": "aws s3 ls"}, None
    )
    assert done["cli_command"] == "aws s3 ls --region eu-west-1"


def test_azure_keeps_to_a_subscription_and_a_resource_group() -> None:
    scope = {"subscription": "sub-acme", "resource_group": "rg-web"}
    takes = ["subscription", "resource-group", "account"]
    done = remote.narrow(spec("azure"), scope, "storage_account_get", {}, takes)
    assert (done["subscription"], done["resource-group"]) == ("sub-acme", "rg-web")
    with pytest.raises(remote.ScopeError, match="limited to"):
        remote.narrow(spec("azure"), scope, "x_get", {"subscription": "sub-globex"}, takes)


def test_the_clouds_a_login_reaches_many_clients_through_need_a_scope() -> None:
    assert [k for k in ("aws", "azure", "gcloud", "oci") if spec(k).required] == [
        "azure", "gcloud", "oci",
    ]  # fmt: skip


def test_every_local_server_is_pinned() -> None:
    for service in remote.CATALOG:
        if service.local:
            assert any("==" in a or "@" in a[1:] for a in service.local), service.key


def test_a_logins_secret_says_how_to_start_the_server_and_holds_no_key() -> None:
    env = {"AWS_API_MCP_PROFILE_NAME": "gi-prod", "AWS_REGION": "us-east-1"}
    spec_json = json.loads(remote_connections.local_secret("aws", env))
    assert spec_json["command"] == "uvx"
    assert spec_json["env"]["AWS_API_MCP_TELEMETRY"] == "false"
    assert spec_json["env"]["AWS_API_MCP_PROFILE_NAME"] == "gi-prod"
    assert "secret" not in json.dumps(spec_json).lower()


def test_cloud_logins_can_connect() -> None:
    login = cli_logins.Login("1", "AWS", "p", "aws", "AWS · p", env=(("A", "b"),))
    assert cli_logins.is_cloud(login)
    assert login.usable
