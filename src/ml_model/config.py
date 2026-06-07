"""Configuracoes do modelo preditivo.

Centraliza paths, seed, threshold de classificacao e hiperparametros.
Os caminhos sao resolvidos relativos ao diretorio do projeto (raiz), para
funcionar tanto rodando local quanto no Streamlit Cloud.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
MODELS_DIR = SRC_DIR / "models"
DATA_DIR = PROJECT_ROOT / "data"

SEED = 42

BEST_MODEL_PATH = MODELS_DIR / "risk_best.joblib"
MODEL_PATHS = {
    "logreg": MODELS_DIR / "risk_logreg.joblib",
    "tree": MODELS_DIR / "risk_tree.joblib",
    "forest": MODELS_DIR / "risk_forest.joblib",
    "gbm": MODELS_DIR / "risk_gbm.joblib",
}

# ── Regressao de IMPACTO_TOTAL (R$) ─────────────────────────────────
# Modelo separado: preve o valor economico do impacto (R$), nao apenas
# se houve evento. Usado para plotar pontos projetados no mesmo eixo Y
# do scatter historico (R$ milhoes) na pagina Score Risk.

BEST_IMPACTO_MODEL_PATH = MODELS_DIR / "impacto_best.joblib"
MODELO_IMPACTO_PATHS = {
    "tweedie": MODELS_DIR / "impacto_tweedie.joblib",
    "forest": MODELS_DIR / "impacto_forest.joblib",
    "ridge": MODELS_DIR / "impacto_ridge.joblib",
}

CORRELATION_CSV = DATA_DIR / "correlation_matrix.csv"
MODEL_COMPARISON_CSV = DATA_DIR / "model_comparison.csv"
CONFUSION_MATRIX_PNG = DATA_DIR / "confusion_matrix.png"
CORRELATION_HEATMAP_PNG = DATA_DIR / "correlation_heatmap.png"

TRAIN_TEST_SPLIT_DATE = "2023-01-01"

CLASSIFICATION_THRESHOLD = 0.5

RISK_THRESHOLDS = [
    (25, "Baixo", "#C8E6C9", "#1B5E20"),
    (50, "Medio", "#FFF9C4", "#856404"),
    (75, "Alto", "#FFE0B2", "#8D4E00"),
    (101, "Critico", "#FFCDD2", "#9B1D20"),
]

FEATURE_COLUMNS_NUMERIC = [
    "temp_mean", "temp_min", "temp_max", "temp_std",
    "umi_mean", "umi_min",
    "vento_mean", "vento_max",
    "heat_index", "faixa_termica",
    "temp_mean_3d", "temp_mean_7d", "temp_mean_30d",
    "umi_min_7d", "umi_min_30d",
    "vento_max_7d", "vento_max_30d",
    "dias_secos_consecutivos",
    "anomalia_temp",
]

FEATURE_COLUMNS_CYCLIC = ["dia_sin", "dia_cos"]
FEATURE_COLUMNS_CATEGORICAL = ["cultura", "regiao"]
TARGET_COLUMN = "houve_evento"
TARGET_IMPACTO_COLUMN = "impacto_total"
TARGET_IMPACTO_AGRI_COLUMN = "impacto_agricultura"

MODEL_HYPERPARAMS = {
    "logreg": {
        "max_iter": 1000,
        "class_weight": "balanced",
        "random_state": SEED,
        "solver": "liblinear",
    },
    "tree": {
        "max_depth": 8,
        "class_weight": "balanced",
        "random_state": SEED,
    },
    "forest": {
        "n_estimators": 300,
        "max_depth": 12,
        "class_weight": "balanced",
        "random_state": SEED,
        "n_jobs": -1,
    },
    "gbm": {
        "n_estimators": 200,
        "max_depth": 3,
        "learning_rate": 0.05,
        "random_state": SEED,
    },
}

MODELO_IMPACTO_HYPERPARAMS = {
    "tweedie": {
        "power": 1.5,
        "alpha": 0.1,
        "max_iter": 1000,
        "link": "log",
    },
    "forest": {
        "n_estimators": 300,
        "max_depth": 12,
        "min_samples_leaf": 5,
        "random_state": SEED,
        "n_jobs": -1,
    },
    "ridge": {
        "alpha": 1.0,
        "random_state": SEED,
    },
}


def risk_label(score: int) -> str:
    """Converte score 0-100 em rotulo Baixo/Medio/Alto/Critico."""
    for limite, rotulo, _, _ in RISK_THRESHOLDS:
        if score < limite:
            return rotulo
    return "Critico"


def risk_palette(score: int) -> tuple[str, str]:
    """Retorna (bg_color, fg_color) para um score 0-100."""
    for limite, _, bg, fg in RISK_THRESHOLDS:
        if score < limite:
            return bg, fg
    return RISK_THRESHOLDS[-1][2], RISK_THRESHOLDS[-1][3]
