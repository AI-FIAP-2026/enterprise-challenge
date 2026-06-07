"""Modelo Preditivo de Risco Climático - Campo Seguro (Sprint 2).

Pipeline:
    data.py      -> ETL Oracle -> DataFrame (farm x dia, com label)
    features.py  -> Engenharia de features (agregacoes, janelas, indices)
    train.py     -> Treino + comparacao de 4 modelos
    evaluate.py  -> Matriz de confusao, accuracy, Brier score, correlacao
    predict.py   -> predict_score(uf, municipio, data) -> int 0-100
    config.py    -> paths, seeds, hyperparametros, threshold
"""

from .config import (
    SEED,
    MODELS_DIR,
    DATA_DIR,
    BEST_MODEL_PATH,
    BEST_IMPACTO_MODEL_PATH,
    RISK_THRESHOLDS,
    TRAIN_TEST_SPLIT_DATE,
    risk_label,
    risk_palette,
)
from .data import (
    load_dataset,
    load_dataset_impacto,
    load_climate_recent,
    lookup_farms_in_municipio,
    fetch_open_meteo_forecast,
    attach_farm_metadata,
)
from .features import FeatureBuilder
from .train import compare_models, train_and_save_best, load_best_model
from .train_impacto import (
    compare_regressors,
    train_and_save_best_impacto,
    load_best_impacto_model,
)
from .predict import (
    predict_score,
    predict_proba,
    predict_score_range,
    predict_impacto_range,
    get_farm_context,
)
from .evaluate import evaluate_model, build_correlation_report, build_model_comparison

__all__ = [
    "SEED",
    "MODELS_DIR",
    "DATA_DIR",
    "BEST_MODEL_PATH",
    "BEST_IMPACTO_MODEL_PATH",
    "RISK_THRESHOLDS",
    "TRAIN_TEST_SPLIT_DATE",
    "risk_label",
    "risk_palette",
    "load_dataset",
    "load_dataset_impacto",
    "load_climate_recent",
    "lookup_farms_in_municipio",
    "fetch_open_meteo_forecast",
    "attach_farm_metadata",
    "FeatureBuilder",
    "compare_models",
    "train_and_save_best",
    "load_best_model",
    "compare_regressors",
    "train_and_save_best_impacto",
    "load_best_impacto_model",
    "predict_score",
    "predict_proba",
    "predict_score_range",
    "predict_impacto_range",
    "get_farm_context",
    "evaluate_model",
    "build_correlation_report",
    "build_model_comparison",
]
