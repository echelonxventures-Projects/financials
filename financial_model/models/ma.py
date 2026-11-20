"""
M&A Merger Model.
Accretion/dilution, synergy analysis, pro forma combination.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from financial_model.core.definitions import load_definition
from financial_model.core.expressions import evaluate_values
from financial_model.models.three_statement import ThreeStatementModel


def _row(df: pd.DataFrame, line: str, idx: int, default: float = 0.0) -> float:
    if line not in df.index:
        return default
    val = df.loc[line].iloc[idx]
    return default if pd.isna(val) else float(val)


@dataclass
class MergerModel:
    """
    Merger model for accretion/dilution analysis.
    Combines acquirer and target into a pro forma entity.

    Consideration is split between cash (funded with new debt at ``financing_rate``)
    and acquirer stock (issued at ``acquirer_share_price``; when omitted the price is
    derived from the acquirer's year-1 EPS and ``acquirer_pe``).
    """

    acquirer: ThreeStatementModel
    target: ThreeStatementModel

    # Deal terms: None = the default in models/definitions/ma.yaml (`deal`).
    offer_price_per_share: Any = None
    target_shares: Any = None
    cash_consideration_pct: Any = None
    stock_consideration_pct: Any = None
    synergies: Any = None  # Annual run-rate pre-tax synergies
    synergy_phase_in: Any = None
    financing_rate: Any = None
    tax_rate: Any = None
    acquirer_shares: Any = None
    acquirer_share_price: Any = None
    acquirer_pe: Any = None

    name: Any = None
    model_type = "ma"

    def __post_init__(self) -> None:
        self.definition = load_definition(self.model_type)
        for key, default in (self.definition.get("deal") or {}).items():
            if getattr(self, key, None) is None:
                setattr(self, key, copy.deepcopy(default))
        if self.name is None:
            self.name = str(self.definition.get("name") or type(self).__name__)
        if abs(self.cash_consideration_pct + self.stock_consideration_pct - 1.0) > 1e-9:
            raise ValueError("cash_consideration_pct + stock_consideration_pct must equal 1")
        if self.target_shares < 0 or self.acquirer_shares <= 0:
            raise ValueError("share counts must be positive")

    # ------------------------------------------------------------------ #
    # Config
    # ------------------------------------------------------------------ #
    @classmethod
    def deal_fields(cls) -> tuple[str, ...]:
        """Deal terms a config may set: the keys of the definition's ``deal`` section."""
        return tuple(load_definition(cls.model_type).get("deal") or {})

    @classmethod
    def from_config(cls, config: dict[str, Any], base_dir: Path | None = None) -> MergerModel:
        """Build a merger from a deal config.

        ``acquirer`` and ``target`` are each a path to a model YAML (relative to
        ``base_dir``) or an inline model config; ``deal`` holds the transaction terms::

            model_type: ma
            acquirer: acquirer.yaml
            target: target.yaml
            deal: {offer_price_per_share: 40, target_shares: 1000000, synergies: 1500000}
        """
        from financial_model.models.registry import build_model, normalize_model_type

        base = Path(base_dir) if base_dir is not None else Path.cwd()
        sides = {}
        for side in ("acquirer", "target"):
            spec = config.get(side)
            if spec is None:
                raise ValueError(f"M&A config needs an '{side}' model (path or inline config)")
            if isinstance(spec, (str, Path)):
                path = Path(spec) if Path(spec).is_absolute() else base / spec
                sub_config, sub_base = ThreeStatementModel.load_config(path), path.parent
            elif isinstance(spec, dict):
                sub_config, sub_base = spec, base
            else:
                raise ValueError(f"'{side}' must be a path or a mapping")
            allowed = load_definition(cls.model_type).get("side_model_types") or []
            if normalize_model_type(sub_config.get("model_type")) not in allowed:
                raise ValueError(f"'{side}' must be one of these model types: {', '.join(allowed)}")
            sides[side] = build_model(sub_config, base_dir=sub_base)

        deal = dict(config.get("deal") or {})
        allowed_terms = cls.deal_fields()
        unknown = set(deal) - set(allowed_terms)
        if unknown:
            raise ValueError(f"Unknown deal terms: {', '.join(sorted(unknown))}. Allowed: {', '.join(allowed_terms)}")
        if "cash_consideration_pct" in deal and "stock_consideration_pct" not in deal:
            deal["stock_consideration_pct"] = 1.0 - float(deal["cash_consideration_pct"])
        elif "stock_consideration_pct" in deal and "cash_consideration_pct" not in deal:
            deal["cash_consideration_pct"] = 1.0 - float(deal["stock_consideration_pct"])
        return cls(
            acquirer=sides["acquirer"],
            target=sides["target"],
            name=config.get("name"),
            **{k: v for k, v in deal.items() if k in allowed_terms},
        )

    def _results(self, scenario: str | None) -> tuple[Any, Any]:
        """Acquirer and target results; a scenario applies to each side that defines it."""

        def run(model: ThreeStatementModel) -> Any:
            use = scenario if scenario and scenario in model.assumptions.scenarios else None
            return model.calculate(scenario=use)

        return run(self.acquirer), run(self.target)

    # ------------------------------------------------------------------ #
    def _phase_in(self, idx: int) -> float:
        if not self.synergy_phase_in:
            return 1.0
        return self.synergy_phase_in[min(idx, len(self.synergy_phase_in) - 1)]

    def _share_price(self, acq_eps: float, acq_is: pd.DataFrame) -> float:
        if self.acquirer_share_price is not None and self.acquirer_share_price > 0:
            return self.acquirer_share_price
        if acq_eps > 0:
            return acq_eps * self.acquirer_pe
        # Loss-making acquirer: fall back to a revenue multiple
        fallback = self.definition.get("loss_making_acquirer") or {}
        revenue_per_share = (
            _row(acq_is, self.acquirer.line_name("revenue"), 0) / self.acquirer_shares if self.acquirer_shares else 0.0
        )
        return max(revenue_per_share * float(fallback["revenue_multiple"]), float(fallback["min_share_price"]))

    def calculate_accretion_dilution(self, scenario: str | None = None) -> dict[str, Any]:
        """Run accretion/dilution analysis for every projected period (year-1 keys kept for convenience)."""
        acq_result, tgt_result = self._results(scenario)
        acq_is = acq_result.income_statement
        tgt_is = tgt_result.income_statement
        n_periods = min(len(acq_result.periods), len(tgt_result.periods))

        a_line, t_line = self.acquirer.line_name, self.target.line_name
        acq_ni_0 = _row(acq_is, a_line("net_income"), 0)
        acq_eps_0 = acq_ni_0 / self.acquirer_shares if self.acquirer_shares > 0 else 0.0

        equity_value = self.offer_price_per_share * self.target_shares
        cash_paid = equity_value * self.cash_consideration_pct
        stock_value = equity_value * self.stock_consideration_pct
        share_price = self._share_price(acq_eps_0, acq_is)
        new_shares = stock_value / share_price if share_price > 0 else 0.0
        pf_shares = self.acquirer_shares + new_shares
        acq_debt = cash_paid
        interest_expense = acq_debt * self.financing_rate

        per_period: list[dict[str, Any]] = []
        for i in range(n_periods):
            acq_ni = _row(acq_is, a_line("net_income"), i)
            tgt_ni = _row(tgt_is, t_line("net_income"), i)
            acq_eps = acq_ni / self.acquirer_shares if self.acquirer_shares > 0 else 0.0
            synergies = self.synergies * self._phase_in(i)
            # Pro forma NI = standalone NIs + after-tax synergies - after-tax acquisition interest
            pf_ni = acq_ni + tgt_ni + (synergies - interest_expense) * (1 - self.tax_rate)
            pf_eps = pf_ni / pf_shares if pf_shares > 0 else 0.0
            accretion = (pf_eps - acq_eps) / abs(acq_eps) if acq_eps != 0 else 0.0
            per_period.append(
                {
                    "period": acq_result.periods[i].label,
                    "acquirer_eps": acq_eps,
                    "pf_eps": pf_eps,
                    "accretion_dilution_pct": accretion,
                    "pf_revenue": _row(acq_is, a_line("revenue"), i) + _row(tgt_is, t_line("revenue"), i),
                    "pf_ebit": _row(acq_is, a_line("operating_income"), i)
                    + _row(tgt_is, t_line("operating_income"), i)
                    + synergies,
                    "pf_synergies": synergies,
                    "pf_ni": pf_ni,
                }
            )

        first: dict[str, Any] = per_period[0] if per_period else {}
        return {
            "acquirer_ni": acq_ni_0,
            "acquirer_eps": acq_eps_0,
            "target_ni": _row(tgt_is, t_line("net_income"), 0),
            "equity_value": equity_value,
            "cash_paid": cash_paid,
            "stock_value": stock_value,
            "acquirer_share_price": share_price,
            "new_shares_issued": new_shares,
            "pf_revenue": first.get("pf_revenue", 0.0),
            "pf_ebit": first.get("pf_ebit", 0.0) - interest_expense,
            "pf_synergies": first.get("pf_synergies", 0.0),
            "pf_interest": interest_expense,
            "pf_ni": first.get("pf_ni", 0.0),
            "pf_shares": pf_shares,
            "pf_eps": first.get("pf_eps", 0.0),
            "accretion_dilution_pct": first.get("accretion_dilution_pct", 0.0),
            "is_accretive": first.get("accretion_dilution_pct", 0.0) > 0,
            "by_period": per_period,
        }

    def contribution_analysis(self, scenario: str | None = None) -> pd.DataFrame:
        """Show each company's contribution to the pro forma entity (year 1)."""
        acq_result, tgt_result = self._results(scenario)
        acq_is, tgt_is = acq_result.income_statement, tgt_result.income_statement

        lab = self.definition.get("labels") or {}

        def year_one(model: ThreeStatementModel, df: pd.DataFrame) -> dict[str, float]:
            """Every line role's year-1 value, the names a ``contribution`` metric can use."""
            return {role: _row(df, str(line), 0) for role, line in model.lines.items()}

        acq_values, tgt_values = year_one(self.acquirer, acq_is), year_one(self.target, tgt_is)
        rows = []
        for metric in self.definition.get("contribution") or []:
            acq_val = evaluate_values(str(metric["value"]), acq_values)
            tgt_val = evaluate_values(str(metric["value"]), tgt_values)
            combined = acq_val + tgt_val
            rows.append(
                {
                    lab["metric"]: str(metric["label"]),
                    lab["acquirer"]: acq_val,
                    lab["target"]: tgt_val,
                    lab["combined"]: combined,
                    lab["acquirer_share"]: acq_val / combined if combined != 0 else 0.0,
                }
            )
        return pd.DataFrame(rows)

    def analysis_tables(self, scenario: str | None = None) -> dict[str, Any]:
        """Deal summary, accretion/dilution by period and year-1 contribution analysis."""
        ad = self.calculate_accretion_dilution(scenario)
        summary = {k: v for k, v in ad.items() if k != "by_period"}
        lab, tables = self.definition.get("labels") or {}, self.definition.get("tables") or {}
        by_period = pd.DataFrame(ad["by_period"]).set_index("period")
        by_period.index.name = lab["period"]
        return {
            tables["summary"]: summary,
            tables["accretion_dilution"]: by_period,
            tables["contribution"]: self.contribution_analysis(scenario).set_index(lab["metric"]),
        }
