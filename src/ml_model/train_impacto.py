"""Treino e comparacao do regressor de IMPACTO_TOTAL (R$).

Pipeline separado do classificador (train.py). Em vez de prever se houve
evento (binario), este modulo treina modelos de regressao que aprendem
o valor economico do impacto em R$, condicionados ao clima do dia.

`compare_regressors()` treina TweedieGBR (loss projetado para dados
zero-inflated), RandomForestRegressor e Ridge baseline. Rankeia por RMSE
global + MAE_em_dias_com_evento (porque MAE global eh dominado pelos
zeros). Salva o vencedor em impacto_best.joblib.

`train_and_save_best_impacto()` orquestra e persiste.

`load_best_impacto_model()` carrega o bundle vencedor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge, TweedieRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from .config import (
    BEST_IMPACTO_MODEL_PATH,
    DATA_DIR,
    MODELO_IMPACTO_HYPERPARAMS,
    MODELO_IMPACTO_PATHS,
    TARGET_IMPACTO_COLUMN,
)
from .data import load_dataset_impacto, train_test_split_temporal
from .features import FeatureBuilder


@dataclass
class RegressorResult:
    name: str
    model: object
    metrics: dict
    fit_seconds: float


def _build_regressor(name: str):
    cfg = MODELO_IMPACTO_HYPERPARAMS.get(name, {})
    if name == "tweedie":
        return TweedieRegressor(**cfg)
    if name == "forest":
        return RandomForestRegressor(**cfg)
    if name == "ridge":
        return Ridge(**cfg)
    raise ValueError(f"Regressor desconhecido: {name}")


def _evaluate_regressor(
    model, X_test: pd.DataFrame, y_test: pd.Series
) -> dict:
    y_pred = np.clip(model.predict(X_test), 0, None)
    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    mae = float(mean_absolute_error(y_test, y_pred))
    r2 = float(r2_score(y_test, y_pred))

    mask_pos = y_test > 0
    if mask_pos.any():
        mae_pos = float(mean_absolute_error(y_test[mask_pos], y_pred[mask_pos]))
        n_pos = int(mask_pos.sum())
    else:
        mae_pos = float("nan")
        n_pos = 0

    total_real = float(y_test.sum())
    total_pred = float(y_pred.sum())
    soma_ratio = total_pred / total_real if total_real > 0 else float("nan")

    return {
        "rmse": rmse,
        "mae": mae,
        "mae_dias_com_evento": mae_pos,
        "n_dias_com_evento": n_pos,
        "r2": r2,
        "total_real": total_real,
        "total_pred": total_pred,
        "soma_ratio": soma_ratio,
    }


def compare_regressors(df: pd.DataFrame) -> tuple[list[RegressorResult], FeatureBuilder]:
    """Treina os 3 regressores, retorna (resultados, feature_builder)."""
    builder = FeatureBuilder()
    builder.fit(df)

    train_df, test_df = train_test_split_temporal(df, "2023-01-01")
    X_train = builder.transform(train_df)
    X_test = builder.transform(test_df)
    y_train = train_df[TARGET_IMPACTO_COLUMN].astype(float)
    y_test = test_df[TARGET_IMPACTO_COLUMN].astype(float)

    print(
        f"[train_impacto] train_rows={len(X_train)} test_rows={len(X_test)} "
        f"pos_rate_train={(y_train > 0).mean():.4f} "
        f"impacto_medio_train={y_train.mean():.2f}"
    )

    results: list[RegressorResult] = []
    for name in ["tweedie", "forest", "ridge"]:
        t0 = time.time()
        model = _build_regressor(name)
        model.fit(X_train, y_train)
        dt = time.time() - t0
        metrics = _evaluate_regressor(model, X_test, y_test)
        results.append(RegressorResult(name, model, metrics, dt))
        print(
            f"[train_impacto] {name:>8s} | rmse={metrics['rmse']:.0f} "
            f"mae={metrics['mae']:.0f} mae_pos={metrics['mae_dias_com_evento']:.0f} "
            f"r2={metrics['r2']:.3f} soma_ratio={metrics['soma_ratio']:.2f} | {dt:.1f}s"
        )
    return results, builder


def rank_regressors(results: list[RegressorResult]) -> list[RegressorResult]:
    """Rankeia por mae_dias_com_evento (desempate por rmse)."""
    return sorted(
        results,
        key=lambda r: (
            r.metrics["mae_dias_com_evento"]
            if not np.isnan(r.metrics["mae_dias_com_evento"])
            else float("inf"),
            r.metrics["rmse"],
        ),
    )


def train_and_save_best_impacto(df: Optional[pd.DataFrame] = None) -> dict:
    """Treina todos, persiste os .joblib, salva o melhor como impacto_best.joblib."""
    df = df if df is not None else load_dataset_impacto()
    MODELS_DIR = Path(BEST_IMPACTO_MODEL_PATH).parent
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    results, builder = compare_regressors(df)

    for r in results:
        joblib.dump(
            {"model": r.model, "builder": builder, "model_name": r.name},
            MODELO_IMPACTO_PATHS[r.name],
        )
        print(f"[train_impacto] saved {r.name} -> {MODELO_IMPACTO_PATHS[r.name]}")

    ranked = rank_regressors(results)
    winner = ranked[0]
    joblib.dump(
        {
            "model": winner.model,
            "builder": builder,
            "model_name": winner.name,
            "metrics": winner.metrics,
        },
        BEST_IMPACTO_MODEL_PATH,
    )
    print(
        f"[train_impacto] BEST: {winner.name} "
        f"(mae_pos={winner.metrics['mae_dias_com_evento']:.0f}) "
        f"-> {BEST_IMPACTO_MODEL_PATH}"
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    comparison = pd.DataFrame(
        [
            {
                "model": r.name,
                "rmse": r.metrics["rmse"],
                "mae": r.metrics["mae"],
                "mae_dias_com_evento": r.metrics["mae_dias_com_evento"],
                "n_dias_com_evento": r.metrics["n_dias_com_evento"],
                "r2": r.metrics["r2"],
                "soma_ratio": r.metrics["soma_ratio"],
                "fit_seconds": round(r.fit_seconds, 2),
            }
            for r in results
        ]
    ).sort_values("mae_dias_com_evento", ascending=False)
    comparison.to_csv(DATA_DIR / "modelo_impacto_comparison.csv", index=False)
    print(f"[train_impacto] comparison -> {DATA_DIR / 'modelo_impacto_comparison.csv'}")

    return {
        "winner": winner.name,
        "metrics": winner.metrics,
        "comparison": comparison,
        "model_paths": {r.name: str(MODELO_IMPACTO_PATHS[r.name]) for r in results},
    }


def load_best_impacto_model() -> dict:
    """Carrega o .joblib do regressor vencedor."""
    if not BEST_IMPACTO_MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Modelo de impacto nao encontrado em {BEST_IMPACTO_MODEL_PATH}. "
            "Execute `python -m ml.py impacto train`."
        )
    bundle = joblib.load(BEST_IMPACTO_MODEL_PATH)
    return bundle


def load_impacto_model_by_name(name: str) -> dict:
    """Carrega um regressor especifico pelo nome (tweedie/forest/ridge)."""
    path = MODELO_IMPACTO_PATHS[name]
    if not path.exists():
        raise FileNotFoundError(f"Modelo de impacto {name} nao encontrado em {path}")
    return joblib.load(path)
