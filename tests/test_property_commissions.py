"""Regression tests for confirmed property commission rules."""

import pytest

from app.services.property_commissions import (
    DEFAULT_BUYER_COMMISSION_BPS,
    DEFAULT_PROPERTY_LISTING_FEE_MINOR,
    DEFAULT_SELLER_COMMISSION_BPS,
    PROPERTY_CURRENCY,
    PropertyCommissionBreakdown,
    calculate_property_commissions,
)


def test_confirmed_property_fee_and_commission_defaults_are_explicit():
    assert PROPERTY_CURRENCY == "PKR"
    assert DEFAULT_PROPERTY_LISTING_FEE_MINOR == 50_000
    assert DEFAULT_BUYER_COMMISSION_BPS == 100
    assert DEFAULT_SELLER_COMMISSION_BPS == 100


def test_buyer_and_seller_commissions_use_integer_minor_units():
    result = calculate_property_commissions(1_000_000)

    assert result == PropertyCommissionBreakdown(
        buyer_commission_minor=10_000,
        seller_commission_minor=10_000,
        total_commission_minor=20_000,
    )


def test_commission_rounding_is_half_up_without_float_arithmetic():
    result = calculate_property_commissions(50)

    assert result.buyer_commission_minor == 1
    assert result.seller_commission_minor == 1
    assert result.total_commission_minor == 2


def test_commission_rates_can_be_configured_without_changing_the_price_unit():
    result = calculate_property_commissions(
        20_000,
        buyer_rate_bps=125,
        seller_rate_bps=75,
    )

    assert result.buyer_commission_minor == 250
    assert result.seller_commission_minor == 150
    assert result.total_commission_minor == 400


@pytest.mark.parametrize("price", [0, -1, 9_000_000_000_000_001, True, 1.5, "1000"])
def test_invalid_sale_prices_are_rejected(price):
    with pytest.raises(ValueError):
        calculate_property_commissions(price)


@pytest.mark.parametrize(
    ("buyer_rate", "seller_rate"),
    [(-1, 100), (10_001, 100), (100, -1), (100, 10_001), (True, 100), (100, 1.5)],
)
def test_invalid_commission_rates_are_rejected(buyer_rate, seller_rate):
    with pytest.raises(ValueError):
        calculate_property_commissions(
            100_000,
            buyer_rate_bps=buyer_rate,
            seller_rate_bps=seller_rate,
        )
