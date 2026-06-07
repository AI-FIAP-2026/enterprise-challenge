"""Avaliacao do modelo: matriz de confusao, correlacao, relatorio comparativo.

Gera os artefatos exigidos pelo checklist da Sprint 2:
- data/confusion_matrix.png   (heatmap)
- data/correlation_matrix.csv (Pearson)
- data/correlation_heatmap.png
- data/model_comparison.csv   (leaderboard F1)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.metrics import confusion_matrix

from .config import (
    CONFUSION_MATRIX_PNG,
    CORRELATION_CSV,
    CORRELATION_HEATMAP_PNG,
    DATA_DIR,
    FEATURE_COLUMNS_NUMERIC,
    MODEL_COMPARISON_CSV,
    TRAIN_TEST_SPLIT_DATE,
)
from .data import load_dataset, train_test_split_temporal


def build_correlation_report(
    df: Optional[pd.DataFrame] = None,
    columns: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Calcula matriz de correlacao de Pearson das features numericas + label.

    Salva em data/correlation_matrix.csv e data/correlation_heatmap.png.
    """
    df = df if df is not None else load_dataset()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if columns is None:
        columns = list(FEATURE_COLUMNS_NUMERIC) + ["houve_evento"]

    available = [c for c in columns if c in df.columns]
    corr = df[available].corr(numeric_only=True)

    corr.to_csv(CORRELATION_CSV)
    print(f"[evaluate] correlation -> {CORRELATION_CSV}")

    fig, ax = plt.subplots(figsize=(14, 10))
    sns.heatmap(
        corr, annot=True, fmt=".2f", cmap="RdBu_r", center=0,
        vmin=-1, vmax=1, square=False, cbar_kws={"shrink": 0.8}, ax=ax,
    )
    ax.set_title("Correlacao de Pearson - features climaticas x label", fontsize=14)
    plt.tight_layout()
    fig.savefig(CORRELATION_HEATMAP_PNG, dpi=120)
    plt.close(fig)
    print(f"[evaluate] heatmap -> {CORRELATION_HEATMAP_PNG}")
    return corr


def plot_confusion_matrix(y_true, y_pred, title: str = "Matriz de Confusao") -> Path:
    """Salva heatmap da matriz de confusao em data/confusion_matrix.png."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    fig, ax = plt.subplots(figsize=(6, 5))
    sns.heatmap(
        cm, annot=True, fmt="d", cmap="Blues", cbar=False,
        xticklabels=["Sem evento (0)", "Com evento (1)"],
        yticklabels=["Sem evento (0)", "Com evento (1)"], ax=ax,
    )
    ax.set_xlabel("Predito")
    ax.set_ylabel("Real")
    ax.set_title(title, fontsize=14)
    plt.tight_layout()
    fig.savefig(CONFUSION_MATRIX_PNG, dpi=120)
    plt.close(fig)
    print(f"[evaluate] confusion matrix -> {CONFUSION_MATRIX_PNG}")
    return CONFUSION_MATRIX_PNG


def evaluate_model(
    bundle: dict, df: Optional[pd.DataFrame] = None, threshold: float = 0.5
) -> dict:
    """Avalia um bundle {model, builder} no split temporal e gera artefatos."""
    df = df if df is not None else load_dataset()
    model = bundle["model"]
    builder = bundle["builder"]
    model_name = bundle.get("model_name", "model")

    train_df, test_df = train_test_split_temporal(df, TRAIN_TEST_SPLIT_DATE)
    X_test = builder.transform(test_df)
    y_test = test_df["houve_evento"].astype(int)

    y_proba = model.predict_proba(X_test)[:, 1]
    y_pred = (y_proba >= threshold).astype(int)

    cm_path = plot_confusion_matrix(
        y_test, y_pred, title=f"Matriz de Confusao - {model_name}"
    )

    from sklearn.metrics import (
        accuracy_score,
        brier_score_loss,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )
    metrics = {
        "model": model_name,
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "precision": float(precision_score(y_test, y_pred, zero_division=0)),
        "recall": float(recall_score(y_test, y_pred, zero_division=0)),
        "f1": float(f1_score(y_test, y_pred, zero_division=0)),
        "brier": float(brier_score_loss(y_test, y_proba)),
        "auc_roc": float(roc_auc_score(y_test, y_proba)) if y_test.nunique() > 1 else float("nan"),
        "n_test": int(len(y_test)),
        "n_pos_test": int(y_test.sum()),
        "confusion_matrix_png": str(cm_path),
    }
    print(f"[evaluate] {model_name}: {metrics}")
    return metrics


def build_model_comparison() -> Optional[pd.DataFrame]:
    """Le o CSV gerado pelo train (se existir) e retorna DataFrame ordenado."""
    if not MODEL_COMPARISON_CSV.exists():
        return None
    return pd.read_csv(MODEL_COMPARISON_CSV).sort_values("f1", ascending=False)


def top_correlations_with_label(
    corr: pd.DataFrame, label: str = "houve_evento", top_k: int = 10
) -> pd.DataFrame:
    """Retorna as top_k features mais correlacionadas (em valor absoluto) com o label."""
    if label not in corr.columns:
        return pd.DataFrame()
    s = corr[label].drop(label).abs().sort_values(ascending=False).head(top_k)
    return pd.DataFrame({"feature": s.index, "abs_corr": s.values})
