from models.base_agent import CallData
from services.email_settings import EmailSettings
from services.sip_metadata import participant_fields


def test_email_settings_validate_required_values():
    env = {
        "SENDGRID_API_KEY": "SG_test",
        "EMAIL_FROM": "sender@domain.test",
        "EMAIL_TO": "recipient@domain.test",
    }
    assert EmailSettings.from_env(env).email_to == env["EMAIL_TO"]
    env["EMAIL_TO"] = "recipient@example.com"
    try:
        EmailSettings.from_env(env)
    except ValueError as exc:
        assert "placeholder" in str(exc)
    else:
        raise AssertionError("placeholder recipient was accepted")


def test_sip_caller_metadata_is_captured_without_erasing_values():
    data = CallData(sip_call_id="already-known")

    class Participant:
        def __init__(self):
            self.identity = "sip_123"
            self.attributes = {
                "sip.phoneNumber": "+1 (212) 555-0100",
                "sip.trunkID": "TR_1",
            }

    participant_fields(data, Participant())
    assert data.caller_number == "+12125550100"
    assert data.caller_number_original == "+1 (212) 555-0100"
    assert data.sip_call_id == "already-known"
    assert data.sip_trunk_id == "TR_1"
