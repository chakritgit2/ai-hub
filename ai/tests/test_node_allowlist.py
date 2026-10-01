from app.services.node_allowlist import allowed_node_types, find_disallowed_types


def test_developer_does_not_get_admin_only_types():
    allowed = allowed_node_types("developer")
    assert "dynamiq.nodes.tools.python.Python" not in allowed
    assert "dynamiq.connections.OpenAI" in allowed


def test_admin_gets_admin_only_types_too():
    allowed = allowed_node_types("admin")
    assert "dynamiq.nodes.tools.python.Python" in allowed
    assert "dynamiq.connections.OpenAI" in allowed


def test_viewer_is_treated_like_a_non_admin_role():
    assert allowed_node_types("viewer") == allowed_node_types("developer")


def test_find_disallowed_types_walks_nested_dicts_and_lists():
    tree = {
        "agent": {
            "type": "dynamiq.nodes.agents.Agent",
            "tools": [
                {"type": "dynamiq.nodes.tools.python.Python"},
                {"type": "dynamiq.nodes.tools.http_api_call.HttpApiCall"},
            ],
            "llm": {"type": "dynamiq.nodes.llms.OpenAI", "connection": {"type": "dynamiq.connections.OpenAI"}},
        }
    }

    assert find_disallowed_types(tree, "developer") == [
        "dynamiq.nodes.tools.python.Python",
        "dynamiq.nodes.tools.http_api_call.HttpApiCall",
    ]
    assert find_disallowed_types(tree, "admin") == ["dynamiq.nodes.tools.http_api_call.HttpApiCall"]


def test_find_disallowed_types_ignores_non_string_type_values():
    tree = {"agent": {"type": None, "llm": {"type": "dynamiq.nodes.llms.OpenAI"}}}
    assert find_disallowed_types(tree, "developer") == []


def test_find_disallowed_types_on_empty_tree():
    assert find_disallowed_types({}, "developer") == []
