from app.services.validator import ValidationResult


def test_validation_result_shape():
    result = ValidationResult(
        status="valid",
        campaign="claude_code_guest_pass_a47c",
        is_valid=True,
    )
    assert result.status == "valid"
    assert result.is_valid is True
