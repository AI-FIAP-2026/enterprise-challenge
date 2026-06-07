"""Treino e comparacao de modelos.

`compare_models()` treina os 4 algoritmos com os mesmos hiperparametros
configurados, avalia no split temporal e retorna um ranking por F1.

`train_and_save_best()` itera o mesmo pipeline, persiste cada modelo em
src/models/risk_<nome>.joblib e salva o vencedor em risk_best.joblib.

`load_best_model()` carrega o joblib vencedor junto com o FeatureBuilder
empacotado (chave: ("model", "builder")).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.tree import DecisionTreeClassifier

from .config import (
    BEST_MODEL_PATH,
    DATA_DIR,
    MODEL_HYPERPARAMS,
    MODEL_PATHS,
    SEED,
)
from .data import load_dataset, train_test_split_temporal
from .features import FeatureBuilder


@dataclass
class ModelResult:
    name: str
    model: object
    metrics: dict
    fit_seconds: float


def _build_model(name: str):
    cfg = MODEL_HYPERPARAMS.get(name, {})
    if name == "logreg":
        return LogisticRegression(**cfg)
    if name == "tree":
        return DecisionTreeClassifier(**cfg)
    if name == "forest":
        return RandomForestClassifier(**cfg)
    if name == "gbm":
        return GradientBoostingClassifier(**cfg)
    raise ValueError(f"Modelo desconhecido: {name}")


def _evaluate(model, X_test: pd.DataFrame, y_test: pd.Series) -> dict:
    y_proba = model.predict_proba(X_test)[:, 1]
    y_pred = (y_proba >= 0.5).astype(int)

    metrics = {
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "precision": float(precision_score(y_test, y_pred, zero_division=0)),
        "recall": float(recall_score(y_test, y_pred, zero_division=0)),
        "f1": float(f1_score(y_test, y_pred, zero_division=0)),
        "brier": float(brier_score_loss(y_test, y_proba)),
    }
    try:
        metrics["auc_roc"] = float(roc_auc_score(y_test, y_proba))
    except ValueError:
        metrics["auc_roc"] = float("nan")
    return metrics


def compare_models(df: pd.DataFrame) -> tuple[list[ModelResult], FeatureBuilder]:
    """Treina todos os modelos, retorna (resultados, feature_builder)."""
    builder = FeatureBuilder()
    builder.fit(df)

    train_df, test_df = train_test_split_temporal(df, "2023-01-01")
    X_train = builder.transform(train_df)
    X_test = builder.transform(test_df)
    y_train = train_df["houve_evento"].astype(int)
    y_test = test_df["houve_evento"].astype(int)

    print(f"[train] train_rows={len(X_train)} test_rows={len(X_test)} "
          f"pos_rate_train={y_train.mean():.4f} pos_rate_test={y_test.mean():.4f}")

    results: list[ModelResult] = []
    for name in ["logreg", "tree", "forest", "gbm"]:
        t0 = time.time()
        model = _build_model(name)
        model.fit(X_train, y_train)
        dt = time.time() - t0
        metrics = _evaluate(model, X_test, y_test)
        results.append(ModelResult(name, model, metrics, dt))
        print(
            f"[train] {name:>8s} | acc={metrics['accuracy']:.3f} "
            f"f1={metrics['f1']:.3f} auc={metrics['auc_roc']:.3f} "
            f"brier={metrics['brier']:.4f} | {dt:.1f}s"
        )
    return results, builder


def rank_models(results: list[ModelResult]) -> list[ModelResult]:
    return sorted(results, key=lambda r: r.metrics["f1"], reverse=True)


def train_and_save_best(df: Optional[pd.DataFrame] = None) -> dict:
    """Treina todos, persiste os .joblib, salva o melhor como risk_best.joblib."""
    df = df if df is not None else load_dataset()
    MODELS_DIR = Path(BEST_MODEL_PATH).parent
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    results, builder = compare_models(df)

    for r in results:
        joblib.dump(
            {"model": r.model, "builder": builder, "model_name": r.name},
            MODEL_PATHS[r.name],
        )
        print(f"[train] saved {r.name} -> {MODEL_PATHS[r.name]}")

    ranked = rank_models(results)
    winner = ranked[0]
    joblib.dump(
        {
            "model": winner.model,
            "builder": builder,
            "model_name": winner.name,
            "metrics": winner.metrics,
        },
        BEST_MODEL_PATH,
    )
    print(f"[train] BEST: {winner.name} (f1={winner.metrics['f1']:.3f}) -> {BEST_MODEL_PATH}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    comparison = pd.DataFrame(
        [
            {
                "model": r.name,
                "accuracy": r.metrics["accuracy"],
                "precision": r.metrics["precision"],
                "recall": r.metrics["recall"],
                "f1": r.metrics["f1"],
                "auc_roc": r.metrics["auc_roc"],
                "brier": r.metrics["brier"],
                "fit_seconds": round(r.fit_seconds, 2),
            }
            for r in results
        ]
    ).sort_values("f1", ascending=False)
    comparison.to_csv(DATA_DIR / "model_comparison.csv", index=False)
    print(f"[train] comparison -> {DATA_DIR / 'model_comparison.csv'}")

    return {
        "winner": winner.name,
        "metrics": winner.metrics,
        "comparison": comparison,
        "model_paths": {r.name: str(MODEL_PATHS[r.name]) for r in results},
    }


def load_best_model() -> dict:
    """Carrega o .joblib do vencedor. Retorna dict com chaves model, builder, model_name."""
    if not BEST_MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Modelo nao encontrado em {BEST_MODEL_PATH}. "
            "Execute `python -m ml_model.train` ou chame train_and_save_best()."
        )
    bundle = joblib.load(BEST_MODEL_PATH)
    return bundle


def load_model_by_name(name: str) -> dict:
    """Carrega um .joblib especifico pelo nome (logreg/tree/forest/gbm)."""
    path = MODEL_PATHS[name]
    if not path.exists():
        raise FileNotFoundError(f"Modelo {name} nao encontrado em {path}")
    return joblib.load(path)
