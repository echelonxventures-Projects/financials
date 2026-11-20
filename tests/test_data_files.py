"""Runtime settings, synthetic profiles and import mappings are data files, extendable from settings."""

from __future__ import annotations

import datetime

import pandas as pd
import pytest
import yaml

from financial_model import config_loader
from financial_model.data import synthetic
from financial_model.data.loader import derive_drivers_from_financials, load_mappings


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    def write(doc):
        path = tmp_path / "financial.yaml"
        path.write_text(yaml.safe_dump(doc), encoding="utf-8")
        monkeypatch.setenv("FIN_CONFIG_PATH", str(path))
        config_loader._reset_cache()
        return path

    yield write
    monkeypatch.delenv("FIN_CONFIG_PATH", raising=False)
    config_loader._reset_cache()


def test_builtin_settings_come_from_yaml():
    with config_loader.Path(config_loader.__file__).with_name("settings.yaml").open(encoding="utf-8") as f:
        shipped = yaml.safe_load(f)
    cfg = config_loader.get_config(builtin=None)
    assert cfg["api"]["port"] == shipped["api"]["port"]
    assert cfg["defaults"]["industry"] == shipped["defaults"]["industry"]


def test_start_year_defaults_to_this_year(settings_file):
    assert config_loader.default("start_year") == datetime.date.today().year
    settings_file({"defaults": {"start_year": 2031}})
    assert config_loader.default("start_year") == 2031


def test_synthetic_profiles_extend_and_add(settings_file, tmp_path):
    base = synthetic.profiles()
    assert {"saas", "consumer", "manufacturing"} <= set(base)
    assert base["consumer"]["ar_days"] == base["saas"]["ar_days"]  # inherited
    extra = tmp_path / "profiles.yaml"
    extra.write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "fintech": {"extends": "saas", "name": "Fintech Co", "label": "Fintech", "gross_margin": 0.6},
                    "manufacturing": None,
                }
            }
        ),
        encoding="utf-8",
    )
    settings_file({"data": {"synthetic_paths": [str(extra)]}})
    found = synthetic.profiles()
    assert "fintech" in found and "manufacturing" not in found
    data = synthetic.generate_company("fintech", years=3, base_arr=1_000_000)
    assert data["metadata"]["industry"] == "Fintech" and len(data["periods"]) == 3
    is_df = data["income_statement"]
    assert is_df.loc["COGS"].iloc[0] == pytest.approx(is_df.loc["Revenue"].iloc[0] * 0.4)
    with pytest.raises(ValueError, match="unknown synthetic industry"):
        synthetic.generate_company("nope")


def test_import_mappings_extend(settings_file, tmp_path):
    assert load_mappings()["line_mappings"]["income_statement"]["Total Revenue"] == "Revenue"
    extra = tmp_path / "mappings.yaml"
    extra.write_text(
        yaml.safe_dump(
            {"line_mappings": {"income_statement": {"Umsatz": "Revenue"}}, "derivation": {"growth": {"long_run": 0.05}}}
        ),
        encoding="utf-8",
    )
    settings_file({"data": {"mapping_paths": [str(extra)]}})
    m = load_mappings()
    assert m["line_mappings"]["income_statement"]["Umsatz"] == "Revenue"
    assert m["line_mappings"]["income_statement"]["Total Revenue"] == "Revenue"  # built-ins kept
    is_df = pd.DataFrame({"FY2023": [100.0], "FY2024": [100.0]}, index=["Revenue"])
    growth = derive_drivers_from_financials(is_df, None, None, projection_years=3)["revenue_growth"]["value"]
    assert growth[-1] == pytest.approx(0.05 + (0.0 - 0.05) * 0.9**2, abs=1e-4)
