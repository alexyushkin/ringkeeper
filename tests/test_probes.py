"""What the Test button says."""


def test_airtable_s_own_server_says_whether_it_offers_automations() -> None:
    from ringkeeper.adapters.base import Target  # noqa: PLC0415
    from ringkeeper.models import Env  # noqa: PLC0415
    from ringkeeper.services import probes  # noqa: PLC0415
    from ringkeeper.vault import Secret  # noqa: PLC0415

    own = Target("acme-airtable-mcp", Env.PROD, None, Secret("r", "x"), {"remote": "airtable"})
    names = ["list_bases", "list_automations", "update_automation"]
    said = probes._looked_for(own, names)  # pyright: ignore[reportPrivateUsage]
    assert said == "Automations: list_automations, update_automation."
    assert probes._looked_for(own, ["list_bases"]) == "No automations tools among them."  # pyright: ignore[reportPrivateUsage]
    other = Target("crm", Env.PROD, None, Secret("r", "x"), {"remote": "notion"})
    assert probes._looked_for(other, names) == ""  # pyright: ignore[reportPrivateUsage]
