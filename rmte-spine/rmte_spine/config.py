from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PACKAGE_ROOT / "data"
DEFAULT_SQLITE = DATA_DIR / "rmte_spine.sqlite"
TLAI_ROOT = PACKAGE_ROOT.parent.parent / "TLAI" / "TLAI"

_ARCHETYPE_ALIASES = {
    "rag": "rag",
    "agent": "agent",
    "agentic": "agent",
    "agentic ai": "agent",
    "chat assistant": "chat_assistant",
    "chat_assistant": "chat_assistant",
    "copilot": "copilot",
    "enterprise copilot": "copilot",
    "computer vision": "computer_vision",
    "computer_vision": "computer_vision",
    "deep learning": "deep_learning",
    "deep_learning": "deep_learning",
    "embodied": "embodied",
    "federated learning": "federated_learning",
    "federated_learning": "federated_learning",
    "fine-tuned llm": "fine_tuned_llm",
    "fine_tuned_llm": "fine_tuned_llm",
    "multimodal": "multimodal",
    "nlp": "nlp",
    "recommender": "recommender",
    "reinforcement learning": "reinforcement_learning",
    "reinforcement_learning": "reinforcement_learning",
    "scientific": "scientific",
    "time-series": "time_series",
    "time_series": "time_series",
    "traditional ml": "traditional_ml",
    "traditional_ml": "traditional_ml",
}

KIND_FILES: dict[str, tuple[str, ...]] = {
    "metrics": ("agent_metrics.json", "rag_metrics.json"),
    "risks": ("agent_risks.json", "rag_risks.json"),
    "risk_metric_map": ("risk_metric_map.json",),
    "formula_inputs": ("formula_inputs.json",),
    "logs_layers": ("logs_layers.json",),
    "metric_pattern_matrix": ("metric_pattern_matrix.json",),
    "category_rules": ("category_rules.json",),
    "scenario_templates": ("scenario_templates.json",),
    "test_case_schema": ("test_case_schema.json",),
    "test_case_templates": ("test_case_templates.json",),
    "case_variant_slots": ("case_variant_slots.json",),
    "llm_options": ("llm_options.json",),
}

REQUIRED_KINDS = (
    "metrics",
    "risks",
    "risk_metric_map",
    "formula_inputs",
    "logs_layers",
    "metric_pattern_matrix",
)

KIND_TO_LIBRARIES_FIELD = {
    "metrics": "metrics_doc",
    "risks": "risks_doc",
    "risk_metric_map": "risk_metric_map",
    "metric_pattern_matrix": "pattern_matrix",
    "category_rules": "category_rules",
    "scenario_templates": "scenario_templates",
    "test_case_schema": "test_case_schema",
    "test_case_templates": "test_case_templates",
    "logs_layers": "logs_layers",
    "formula_inputs": "formula_inputs",
    "case_variant_slots": "case_variant_slots",
}

WORKBOOK_ARCHETYPES: dict[str, dict[str, str]] = {
    "TrustLensAI_Agentic_Logs_Layers_Formulas.xlsx": {
        "id": "agent",
        "display_name": "Agent",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_RAG_Logs_Layers_Formulas.xlsx": {
        "id": "rag",
        "display_name": "RAG",
        "pattern_field": "rag_pattern",
    },
    "TrustLensAI_ChatAssistant_Logs_Layers_Formulas.xlsx": {
        "id": "chat_assistant",
        "display_name": "Chat Assistant",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_ComputerVision_Logs_Layers_Formulas.xlsx": {
        "id": "computer_vision",
        "display_name": "Computer Vision",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_DeepLearning_Logs_Layers_Formulas.xlsx": {
        "id": "deep_learning",
        "display_name": "Deep Learning",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_Embodied_Logs_Layers_Formulas.xlsx": {
        "id": "embodied",
        "display_name": "Embodied",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_EnterpriseCopilot_Logs_Layers_Formulas.xlsx": {
        "id": "copilot",
        "display_name": "Enterprise Copilot",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_FederatedLearning_Logs_Layers_Formulas.xlsx": {
        "id": "federated_learning",
        "display_name": "Federated Learning",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_FineTunedLLM_Logs_Layers_Formulas.xlsx": {
        "id": "fine_tuned_llm",
        "display_name": "Fine-Tuned LLM",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_Multimodal_Logs_Layers_Formulas.xlsx": {
        "id": "multimodal",
        "display_name": "Multimodal",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_NLP_Logs_Layers_Formulas.xlsx": {
        "id": "nlp",
        "display_name": "NLP",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_Recommender_Logs_Layers_Formulas.xlsx": {
        "id": "recommender",
        "display_name": "Recommender",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_ReinforcementLearning_Logs_Layers_Formulas.xlsx": {
        "id": "reinforcement_learning",
        "display_name": "Reinforcement Learning",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_Scientific_Logs_Layers_Formulas.xlsx": {
        "id": "scientific",
        "display_name": "Scientific",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_TimeSeries_Logs_Layers_Formulas.xlsx": {
        "id": "time_series",
        "display_name": "Time-Series",
        "pattern_field": "execution_pattern",
    },
    "TrustLensAI_TraditionalML_Logs_Layers_Formulas.xlsx": {
        "id": "traditional_ml",
        "display_name": "Traditional ML",
        "pattern_field": "execution_pattern",
    },
}

ARCHETYPE_META = {
    meta["id"]: {
        "display_name": meta["display_name"],
        "pattern_field": meta["pattern_field"],
        "notes": f"TLAI workbook {filename}",
        "workbook": filename,
    }
    for filename, meta in WORKBOOK_ARCHETYPES.items()
}


def load_env() -> None:
    load_dotenv(PACKAGE_ROOT / ".env")


def normalize_archetype(value: str | None) -> str:
    raw = (value or "rag").strip().lower().replace("-", "_")
    raw = raw.replace("__", "_")
    return _ARCHETYPE_ALIASES.get(raw, raw)


def spine_disabled() -> bool:
    return os.environ.get("RMTE_SPINE_DISABLE", "").strip() in {"1", "true", "yes"}


def database_url() -> str:
    load_env()
    url = (os.environ.get("RMTE_DATABASE_URL") or "").strip()
    if url:
        return url
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + DEFAULT_SQLITE.resolve().as_posix()


def tlai_root() -> Path:
    override = os.environ.get("RMTE_TLAI_ROOT", "").strip()
    if override:
        return Path(override)
    return TLAI_ROOT


def default_library_roots() -> dict[str, Path]:
    libraries = PACKAGE_ROOT.parent / "agent5_rag" / "agent5_rag" / "libraries"
    return {
        "agent": libraries / "agent" / "v1",
        "rag": libraries / "v1",
    }
