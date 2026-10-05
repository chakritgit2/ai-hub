import uuid

from app.services.egress_allowlist import list_egress_allowlist

from .markers import requires_postgres


@requires_postgres
async def test_list_egress_allowlist_returns_companys_own_rows(company_ids, make_egress_allowlist_entry):
    company_id = company_ids()
    make_egress_allowlist_entry(company_id, "api.openai.com")
    make_egress_allowlist_entry(company_id, "*.internal.example.com", port=8443, allow_private_ip=True)

    entries = await list_egress_allowlist(company_id)

    assert {(e.host_pattern, e.port, e.allow_private_ip) for e in entries} == {
        ("api.openai.com", None, False),
        ("*.internal.example.com", 8443, True),
    }


@requires_postgres
async def test_list_egress_allowlist_does_not_return_other_companys_rows(company_ids, make_egress_allowlist_entry):
    company_id = company_ids()
    other_company_id = str(uuid.uuid4())
    make_egress_allowlist_entry(other_company_id, "api.openai.com")

    entries = await list_egress_allowlist(company_id)

    assert entries == []
    assert company_id != other_company_id


@requires_postgres
async def test_list_egress_allowlist_returns_empty_for_company_with_no_rows(company_ids):
    company_id = company_ids()

    entries = await list_egress_allowlist(company_id)

    assert entries == []
