"""Only credentials with a working adapter can be created."""

from app.db.models import ProviderCredential, ProviderKind
from app.gateway.adapters import adapter_for
from app.gateway.schemas import CredentialCreate


def test_existing_gemini_credential_can_still_be_created() -> None:
    body = CredentialCreate.model_validate(
        {"name": "Gemini", "provider": "gemini", "api_key": "test-key"}
    )

    assert body.provider is ProviderKind.GEMINI


def test_gemini_adapter_keeps_its_fixed_host() -> None:
    credential = ProviderCredential(provider=ProviderKind.GEMINI, base_url="https://example.test")
    adapter = adapter_for(credential.provider)

    request = adapter.check_request(credential, "test-key")

    assert request.url == "https://generativelanguage.googleapis.com/v1beta/models"
