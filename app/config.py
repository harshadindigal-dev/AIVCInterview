"""Environment settings and the YAML/JSON domain configuration.

Domain config is loaded once per process; edit the files and restart to pick up
changes (e.g. a new team lead in routing.yaml).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    llm_mode: str = "live"
    openrouter_api_key: str = ""
    triage_model: str = "anthropic/claude-sonnet-5.5"
    eval_models: str = "anthropic/claude-haiku-5.5,google/gemini-3.8-flash"
    # OpenRouter `models` fallback list for the app pipeline (not used in eval,
    # which must measure exactly the configured model). Empty disables it.
    triage_fallback_models: str = "google/gemini-3.8-flash"
    openrouter_app_url: str = "http://localhost:8000"
    openrouter_app_name: str = "Intake Triage Demo"
    database_url: str = f"sqlite:///{ROOT / 'data' / 'app.db'}"
    config_dir: Path = ROOT / "config"
    seed_on_startup: bool = True
    llm_timeout_seconds: float = 60.0
    retry_backoff_seconds: float = 2.0

    @property
    def eval_model_list(self) -> list[str]:
        return [m.strip() for m in self.eval_models.split(",") if m.strip()]

    @property
    def all_models(self) -> list[str]:
        out = [self.triage_model]
        out += [m for m in self.eval_model_list if m not in out]
        return out

    @property
    def fallback_model_list(self) -> list[str]:
        return [m.strip() for m in self.triage_fallback_models.split(",") if m.strip()]

    def validate_for_startup(self) -> None:
        if self.llm_mode != "live":
            raise RuntimeError(
                f"LLM_MODE={self.llm_mode!r} is not supported; this build runs live only (see DECISIONS.md D1)."
            )
        if not self.openrouter_api_key.strip():
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key "
                "(https://openrouter.ai/keys). The app classifies every enquiry with a live model."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


@dataclass(frozen=True)
class Domain:
    taxonomy: dict[str, Any]
    rubric: dict[str, Any]
    routing: dict[str, Any]
    crm: dict[str, Any]

    @property
    def service_line_ids(self) -> list[str]:
        return [s["id"] for s in self.taxonomy["service_lines"]]

    @property
    def outcome_ids(self) -> list[str]:
        """Every value the classifier may return as primary_service_line."""
        return self.service_line_ids + [o["id"] for o in self.taxonomy["non_service_outcomes"]]

    def line_name(self, line_id: str | None) -> str:
        for s in self.taxonomy["service_lines"] + self.taxonomy["non_service_outcomes"]:
            if s["id"] == line_id:
                return s["name"]
        return line_id or "Unknown"

    def lead_for(self, line_id: str) -> dict[str, Any] | None:
        key = self.routing["routing_table"].get(line_id)
        if key is None:
            return None
        return {"key": key, **self.routing["team_leads"][key]}

    def all_leads(self) -> list[dict[str, Any]]:
        return [{"key": k, "line": k, **v} for k, v in self.routing["team_leads"].items()]


def load_domain(config_dir: Path | None = None) -> Domain:
    d = Path(config_dir or get_settings().config_dir)
    return Domain(
        taxonomy=yaml.safe_load((d / "taxonomy.yaml").read_text()),
        rubric=yaml.safe_load((d / "rubric.yaml").read_text()),
        routing=yaml.safe_load((d / "routing.yaml").read_text()),
        crm=json.loads((d / "crm_clients.json").read_text()),
    )


@lru_cache
def get_domain() -> Domain:
    return load_domain()
