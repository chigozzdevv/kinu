import pytest
from browser.identity import normalize


@pytest.mark.parametrize("value", ["/keys/one", "https://issuer.example/keys/one"])
def test_linked_identity_is_restricted_to_its_origin_and_prefix(value: str) -> None:
    assert normalize(value, "/keys/", "https://issuer.example/dashboard") == "one"


@pytest.mark.parametrize(
    "value",
    [
        "https://other.example/keys/one",
        "http://issuer.example/keys/one",
        "/keys/one?token=private",
        "/keys/one#fragment",
        "/other/one",
        "/keys/../one",
        "/keys/%2fadmin",
        "//other.example/keys/one",
        "/keys/",
    ],
)
def test_linked_identity_rejects_ambiguous_or_foreign_links(value: str) -> None:
    with pytest.raises(ValueError):
        normalize(value, "/keys/", "https://issuer.example/dashboard")
