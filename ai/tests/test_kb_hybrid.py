from app.services.kb_hybrid import fuse_rankings


def test_alpha_one_reduces_to_vector_only_ordering():
    result = fuse_rankings(vector_ids=["a", "b", "c"], keyword_ids=["c", "b", "a"], alpha=1.0)

    assert result == ["a", "b", "c"]


def test_alpha_zero_reduces_to_keyword_only_ordering():
    result = fuse_rankings(vector_ids=["a", "b", "c"], keyword_ids=["c", "b", "a"], alpha=0.0)

    assert result == ["c", "b", "a"]


def test_document_present_in_only_one_list_still_surfaces():
    # 'a' only in vector, 'c' only in keyword, 'b' in both (ranked higher in each) - hand
    # computed with k=60: score(b) = .5/61 + .5/60 ~= .01653, score(a) = .5/60 ~= .00833,
    # score(c) = .5/61 ~= .00820.
    result = fuse_rankings(vector_ids=["a", "b"], keyword_ids=["b", "c"], alpha=0.5)

    assert result == ["b", "a", "c"]


def test_blended_alpha_hand_computed_order():
    # k=60: score(x) = .6/60 + .4/61 ~= .016557, score(z) = .6/62 + .4/60 ~= .016344,
    # score(y) = .6/61 + .4/62 ~= .016288.
    result = fuse_rankings(vector_ids=["x", "y", "z"], keyword_ids=["z", "x", "y"], alpha=0.6)

    assert result == ["x", "z", "y"]


def test_empty_lists_return_empty():
    assert fuse_rankings(vector_ids=[], keyword_ids=[], alpha=0.6) == []
