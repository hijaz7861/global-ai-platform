"""Decimal-safe property commission calculations in PKR minor units.

The source workflow charges each side 1% of the mutually confirmed final sale
price. Keep calculation separate from payment verification and settlement.
"""

from dataclasses import dataclass

PROPERTY_CURRENCY = "PKR"
DEFAULT_PROPERTY_LISTING_FEE_MINOR = 50_000  # PKR 500.00
DEFAULT_BUYER_COMMISSION_BPS = 100  # 1.00%
DEFAULT_SELLER_COMMISSION_BPS = 100  # 1.00%
BASIS_POINTS_DENOMINATOR = 10_000
MAX_PROPERTY_AMOUNT_MINOR = 9_000_000_000_000_000


@dataclass(frozen=True, slots=True)
class PropertyCommissionBreakdown:
    buyer_commission_minor: int
    seller_commission_minor: int
    total_commission_minor: int


def _validate_minor_amount(value: int, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer number of PKR minor units")
    if not 1 <= value <= MAX_PROPERTY_AMOUNT_MINOR:
        raise ValueError(f"{field} must be between 1 and {MAX_PROPERTY_AMOUNT_MINOR}")


def _validate_rate_bps(value: int, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer number of basis points")
    if not 0 <= value <= BASIS_POINTS_DENOMINATOR:
        raise ValueError(f"{field} must be between 0 and 10000 basis points")


def _round_positive_minor_units(amount_minor: int, rate_bps: int) -> int:
    """Round half up without binary floating-point or Decimal context drift."""
    return (amount_minor * rate_bps + BASIS_POINTS_DENOMINATOR // 2) // BASIS_POINTS_DENOMINATOR


def calculate_property_commissions(
    final_sale_price_minor: int,
    *,
    buyer_rate_bps: int = DEFAULT_BUYER_COMMISSION_BPS,
    seller_rate_bps: int = DEFAULT_SELLER_COMMISSION_BPS,
) -> PropertyCommissionBreakdown:
    """Calculate both commissions from a mutually confirmed PKR sale price.

    Inputs and outputs are paisa (integer PKR minor units). This function does
    not create payment intents, mark payments verified, or settle a transaction.
    """
    _validate_minor_amount(final_sale_price_minor, field="final_sale_price_minor")
    _validate_rate_bps(buyer_rate_bps, field="buyer_rate_bps")
    _validate_rate_bps(seller_rate_bps, field="seller_rate_bps")

    buyer = _round_positive_minor_units(final_sale_price_minor, buyer_rate_bps)
    seller = _round_positive_minor_units(final_sale_price_minor, seller_rate_bps)
    return PropertyCommissionBreakdown(
        buyer_commission_minor=buyer,
        seller_commission_minor=seller,
        total_commission_minor=buyer + seller,
    )
