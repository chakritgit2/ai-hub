from app.integrations.thai_tokenizer import tokenize


def test_unspaced_thai_sentence_segments_into_multiple_tokens():
    tokens = tokenize("สวัสดีครับยินดีต้อนรับ")

    assert len(tokens) > 1
    assert all(token.strip() for token in tokens)


def test_mixed_thai_english_and_number_text_tokenizes_sensibly():
    tokens = tokenize("product code ABC123 ทดสอบ")

    assert "product" in tokens
    assert "code" in tokens
    assert any("ABC" in token or "123" in token for token in tokens)


def test_empty_string_returns_empty_list():
    assert tokenize("") == []


def test_whitespace_only_string_returns_empty_list():
    assert tokenize("   \n\t  ") == []
