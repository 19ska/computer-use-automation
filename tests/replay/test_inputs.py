"""Tests for runtime input validation (cua.replay.inputs)."""

from decimal import Decimal

import pytest

from cua.artifact.schema import InputParameter
from cua.replay.inputs import InputValidationError, validate_inputs

DECLARED = [
    InputParameter(name="amount", type="decimal", required=True, description="d"),
    InputParameter(name="from_account_id", type="string", required=True, description="d"),
    InputParameter(name="to_account_id", type="string", required=True, description="d"),
    InputParameter(name="note", type="string", required=False, description="d"),
]


def test_validate_inputs_success_with_decimal_conversion():
    resolved = validate_inputs(
        DECLARED, {"amount": "20.00", "from_account_id": "111", "to_account_id": "222"}
    )
    assert resolved["amount"] == Decimal("20.00")
    assert isinstance(resolved["amount"], Decimal)
    assert resolved["from_account_id"] == "111"
    assert resolved["to_account_id"] == "222"


def test_validate_inputs_optional_input_may_be_omitted():
    resolved = validate_inputs(
        DECLARED, {"amount": "20.00", "from_account_id": "111", "to_account_id": "222"}
    )
    assert "note" not in resolved


def test_validate_inputs_missing_required_rejected():
    with pytest.raises(InputValidationError, match="missing required input"):
        validate_inputs(DECLARED, {"amount": "20.00", "from_account_id": "111"})


def test_validate_inputs_unknown_name_rejected():
    with pytest.raises(InputValidationError, match="unknown input"):
        validate_inputs(
            DECLARED,
            {
                "amount": "20.00",
                "from_account_id": "111",
                "to_account_id": "222",
                "surprise": "x",
            },
        )


def test_validate_inputs_invalid_decimal_rejected():
    with pytest.raises(InputValidationError, match="valid decimal"):
        validate_inputs(
            DECLARED, {"amount": "not-a-number", "from_account_id": "111", "to_account_id": "222"}
        )


def test_validate_inputs_boolean_type_conversion():
    declared = [InputParameter(name="flag", type="boolean", required=True, description="d")]
    assert validate_inputs(declared, {"flag": "true"})["flag"] is True
    assert validate_inputs(declared, {"flag": "false"})["flag"] is False


def test_validate_inputs_invalid_boolean_rejected():
    declared = [InputParameter(name="flag", type="boolean", required=True, description="d")]
    with pytest.raises(InputValidationError, match="must be 'true' or 'false'"):
        validate_inputs(declared, {"flag": "maybe"})
