from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class MaxBidInputs:
    conservative_sale_price: float
    repair_cost: float = 0
    legal_cost: float = 0
    monthly_holding_cost: float = 0
    holding_months: float = 6
    taxes: float = 0
    sale_commission_percent: float = 0
    target_profit: float = 0
    risk_reserve: float = 0
    annual_capital_cost_percent: float = 0
    intended_bid: float | None = None


@dataclass(frozen=True, slots=True)
class BidScenario:
    name: str
    sale_price: float
    maximum_bid: float
    expected_profit: float | None
    roi_percent: float | None
    annualized_return_percent: float | None
    breakeven_sale_price: float | None
    holding_months: float


def _validate(inputs: MaxBidInputs) -> None:
    for descriptor in fields(inputs):
        name = descriptor.name
        value = getattr(inputs, name)
        if value is not None and value < 0:
            raise ValueError(f"{name} must be non-negative")
    if inputs.conservative_sale_price <= 0:
        raise ValueError("conservative_sale_price must be positive")
    if inputs.holding_months <= 0:
        raise ValueError("holding_months must be positive")
    if inputs.sale_commission_percent > 100 or inputs.annual_capital_cost_percent > 100:
        raise ValueError("percentage values must not exceed 100")


def calculate_max_bid(inputs: MaxBidInputs) -> dict[str, BidScenario]:
    """Calculate a transparent bid ceiling without using an LLM valuation."""

    _validate(inputs)
    definitions = {
        "pessimistic": (0.90, 1.15, 1.25),
        "base": (1.00, 1.00, 1.00),
        "optimistic": (1.05, 0.95, 0.80),
    }
    result: dict[str, BidScenario] = {}
    for name, (sale_factor, cost_factor, months_factor) in definitions.items():
        sale_price = inputs.conservative_sale_price * sale_factor
        months = max(inputs.holding_months * months_factor, 0.1)
        commission = sale_price * inputs.sale_commission_percent / 100
        non_capital_costs = (
            (inputs.repair_cost + inputs.legal_cost + inputs.taxes + inputs.risk_reserve) * cost_factor
            + inputs.monthly_holding_cost * months
            + commission
        )
        available_before_capital = sale_price - non_capital_costs - inputs.target_profit
        capital_factor = 1 + inputs.annual_capital_cost_percent / 100 * months / 12
        maximum_bid = max(0.0, available_before_capital / capital_factor)

        expected_profit = roi = annualized = breakeven = None
        if inputs.intended_bid is not None:
            capital_cost = inputs.intended_bid * (capital_factor - 1)
            total_invested = inputs.intended_bid + non_capital_costs + capital_cost
            expected_profit = sale_price - total_invested
            breakeven = total_invested
            if total_invested > 0:
                roi = expected_profit / total_invested * 100
                if roi > -100:
                    annualized = ((1 + roi / 100) ** (12 / months) - 1) * 100

        result[name] = BidScenario(
            name=name,
            sale_price=round(sale_price, 2),
            maximum_bid=round(maximum_bid, 2),
            expected_profit=round(expected_profit, 2) if expected_profit is not None else None,
            roi_percent=round(roi, 2) if roi is not None else None,
            annualized_return_percent=round(annualized, 2) if annualized is not None else None,
            breakeven_sale_price=round(breakeven, 2) if breakeven is not None else None,
            holding_months=round(months, 2),
        )
    return result
