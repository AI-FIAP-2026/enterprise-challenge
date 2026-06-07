"""Inferencia: features -> probabilidade de evento -> score 0-100.

`predict_score(uf, municipio, data)` retorna um int 0-100. Internamente:
1. Carrega o bundle {model, builder} (cacheado em memoria).
2. Busca os ultimos 90 dias de clima para a fazenda em (uf, municipio).
3. Gera as features via FeatureBuilder.
4. Pega a P(evento) = predict_proba[:, 1].
5. Converte para score 0-100 e devolve o rotulo (Baixo/Medio/Alto/Critico).

`predict_score_range(uf, municipio, days)` faz o mesmo para os proximos N dias
usando a previsao do Open-Meteo. Retorna um DataFrame com colunas
[date, score, probabilidade, rotulo] e metadados [temp_mean, umi_min, vento_max]
para graficos no dashboard.

`predict_impacto_range(uf, municipio, days)` eh o equivalente para o regressor
de IMPACTO_TOTAL (R$). Retorna DataFrame com [date, impacto_total_previsto,
impacto_agricultura_previsto, ...]. Alimenta o scatter de eventos no
dashboard.
"""

from __future__ import annotations

import datetime as _dt
import functools
from typing import Optional

import numpy as np
import pandas as pd

from .config import risk_label, risk_palette
from .data import (
    _aggregate_farms,
    attach_farm_metadata,
    fetch_open_meteo_forecast,
    load_climate_recent,
    lookup_farms_in_municipio,
)
from .train import load_best_model
from .train_impacto import load_best_impacto_model


_DIAS_HISTORICO = 90
_DIAS_FORECAST_MAX = 14


@functools.lru_cache(maxsize=1)
def _get_bundle() -> dict:
    return load_best_model()


@functools.lru_cache(maxsize=1)
def _get_impacto_bundle() -> dict:
    return load_best_impacto_model()


def _ultima_fazenda_por_municipio(df: pd.DataFrame, uf: str, municipio: str) -> pd.DataFrame:
    """Filtra o historico climatico pela (uf, municipio) e devolve as fazendas correspondentes."""
    mask = (df["estado"].str.upper() == uf.upper()) & (
        df["municipio"].str.upper() == municipio.upper()
    )
    return df.loc[mask].sort_values(["id_fazenda", "dia"]).copy()


def _resolver_fazenda_para_projecao(uf: str, municipio: str) -> Optional[dict]:
    """Resolve fazenda(s) do municipio, agregando se houver mais de uma.

    Retorna dict com {id_fazenda, nome_fazenda, latitude, longitude,
    cultura, tamanho, regiao, n_farms} ou None se nao houver fazenda cadastrada.
    """
    farms = lookup_farms_in_municipio(uf, municipio)
    if not farms:
        return None
    aggregated = _aggregate_farms(farms)
    aggregated["estado"] = uf
    aggregated["municipio"] = municipio
    aggregated["n_farms"] = len(farms)
    return aggregated


def predict_proba(
    uf: str, municipio: str, data: Optional[_dt.date] = None
) -> float:
    """Retorna P(houve_evento) em [0, 1] para a fazenda em (uf, municipio) na data alvo."""
    bundle = _get_bundle()
    model = bundle["model"]
    builder = bundle["builder"]

    df_hist = load_climate_recent(days_back=_DIAS_HISTORICO)
    fazenda_hist = _ultima_fazenda_por_municipio(df_hist, uf, municipio)
    if fazenda_hist.empty:
        return 0.0

    data = data or _dt.date.today()
    ultima = fazenda_hist["dia"].max()
    if hasattr(ultima, "date"):
        ultima_date = ultima.date()
    else:
        ultima_date = ultima

    target_date = pd.Timestamp(data)
    if target_date.date() < ultima_date:
        alvo = fazenda_hist[fazenda_hist["dia"] == pd.Timestamp(data)]
    else:
        alvo = fazenda_hist[fazenda_hist["dia"] == fazenda_hist["dia"].max()]

    if alvo.empty:
        return 0.0

    X = builder.transform(alvo)
    proba = float(model.predict_proba(X)[:, 1].mean())
    return proba


def predict_score(
    uf: str, municipio: str, data: Optional[_dt.date] = None
) -> dict:
    """Retorna dict com score (0-100), rotulo, cor de fundo e cor de texto."""
    p = predict_proba(uf, municipio, data)
    score = int(round(np.clip(p, 0.0, 1.0) * 100))
    bg, fg = risk_palette(score)
    return {
        "score": score,
        "probabilidade": p,
        "rotulo": risk_label(score),
        "bg": bg,
        "fg": fg,
    }


def predict_score_simple(
    uf: str, municipio: str, data: Optional[_dt.date] = None
) -> int:
    """Atalho: retorna apenas o score 0-100 (int)."""
    return predict_score(uf, municipio, data)["score"]


def predict_score_range(
    uf: str, municipio: str, days: int = _DIAS_FORECAST_MAX
) -> pd.DataFrame:
    """Prediz score diario para os proximos N dias (max 14) via Open-Meteo.

    Operacao em nivel de municipio:
        1. Busca fazenda(s) cadastrada(s) em CS_FAZENDAS para (uf, municipio).
        2. Se 0 fazendas -> DataFrame vazio (caller exibe "sem fazenda cadastrada").
        3. Se 1 fazenda -> usa diretamente.
        4. Se 2+ fazendas -> agrega (median lat/lon, mode cultura, mean tamanho).
        5. Busca previsao Open-Meteo para o ponto geografico resolvido.
        6. Aplica FeatureBuilder + modelo treinado em cada dia futuro.
        7. Retorna DataFrame com [date, score, probabilidade, rotulo, temp_max, umi_min, vento_max].
    """
    bundle = _get_bundle()
    model = bundle["model"]
    builder = bundle["builder"]

    days = max(1, min(int(days), _DIAS_FORECAST_MAX))
    farm_info = _resolver_fazenda_para_projecao(uf, municipio)
    if farm_info is None:
        return pd.DataFrame()

    try:
        forecast = fetch_open_meteo_forecast(
            farm_info["latitude"], farm_info["longitude"], days=days
        )
    except Exception:
        return pd.DataFrame()
    if forecast.empty:
        return pd.DataFrame()

    recent = load_climate_recent(days_back=_DIAS_HISTORICO)
    fazenda_hist = _ultima_fazenda_por_municipio(recent, uf, municipio)
    if fazenda_hist.empty:
        return pd.DataFrame()

    forecast = attach_farm_metadata(forecast, farm_info)

    combined = pd.concat(
        [fazenda_hist, forecast], ignore_index=True
    ).sort_values(["id_fazenda", "dia"]).reset_index(drop=True)

    cutoff = fazenda_hist["dia"].max()
    future_only = combined[combined["dia"] > cutoff].copy()
    if future_only.empty:
        return pd.DataFrame()

    if "temp_std" in future_only.columns:
        future_only["temp_std"] = (
            pd.to_numeric(future_only["temp_std"], errors="coerce")
            .ffill()
            .fillna(0.0)
        )
    for col in [
        "temp_mean", "temp_min", "temp_max",
        "umi_mean", "umi_min", "vento_mean", "vento_max",
    ]:
        if col in future_only.columns:
            future_only[col] = pd.to_numeric(future_only[col], errors="coerce")

    X = builder.transform(future_only)
    if X.isna().any().any():
        X = X.fillna(0.0)
    proba = model.predict_proba(X)[:, 1]
    score = (np.clip(proba, 0.0, 1.0) * 100).round().astype(int)

    out = pd.DataFrame({
        "date": future_only["dia"].values,
        "score": score,
        "probabilidade": proba,
        "rotulo": [risk_label(int(s)) for s in score],
        "temp_mean": future_only["temp_mean"].values,
        "temp_max": future_only["temp_max"].values,
        "umi_min": future_only["umi_min"].values,
        "vento_max": future_only["vento_max"].values,
    })
    return out.sort_values("date").reset_index(drop=True)


def get_farm_context(uf: str, municipio: str) -> Optional[dict]:
    """Retorna informacoes da fazenda (ou fazenda agregada) usada na projecao.

    None se nao houver fazenda cadastrada em (uf, municipio).
    """
    return _resolver_fazenda_para_projecao(uf, municipio)


def predict_impacto_range(
    uf: str, municipio: str, days: int = _DIAS_FORECAST_MAX
) -> pd.DataFrame:
    """Prediz IMPACTO_TOTAL (R$) diario para os proximos N dias via Open-Meteo.

    Operacao em nivel de municipio (mesma logica de `_resolver_fazenda_para_projecao`):
        1. Busca fazenda(s) em (uf, municipio). Se vazio -> DataFrame vazio.
        2. Se 2+ -> agrega via `_aggregate_farms`.
        3. Busca previsao Open-Meteo (14 dias) para o ponto geografico resolvido.
        4. Aplica FeatureBuilder + regressor treinado em cada dia futuro.
        5. Retorna DataFrame com [date, impacto_total_previsto, impacto_agricultura_previsto,
            temp_max, umi_min, vento_max, cultura, fazenda].

    O regressor (Tweedie/Forest/Ridge) foi treinado em IMPACTO_TOTAL (R$) vs features
    climaticas. Valores previstos sao truncados em >= 0 (R$ nao pode ser negativo).
    """
    try:
        bundle = _get_impacto_bundle()
    except FileNotFoundError:
        return pd.DataFrame()
    model = bundle["model"]
    builder = bundle["builder"]

    days = max(1, min(int(days), _DIAS_FORECAST_MAX))
    farm_info = _resolver_fazenda_para_projecao(uf, municipio)
    if farm_info is None:
        return pd.DataFrame()

    try:
        forecast = fetch_open_meteo_forecast(
            farm_info["latitude"], farm_info["longitude"], days=days
        )
    except Exception:
        return pd.DataFrame()
    if forecast.empty:
        return pd.DataFrame()

    recent = load_climate_recent(days_back=_DIAS_HISTORICO)
    fazenda_hist = _ultima_fazenda_por_municipio(recent, uf, municipio)
    if fazenda_hist.empty:
        return pd.DataFrame()

    forecast = attach_farm_metadata(forecast, farm_info)

    combined = pd.concat(
        [fazenda_hist, forecast], ignore_index=True
    ).sort_values(["id_fazenda", "dia"]).reset_index(drop=True)

    cutoff = fazenda_hist["dia"].max()
    future_only = combined[combined["dia"] > cutoff].copy()
    if future_only.empty:
        return pd.DataFrame()

    if "temp_std" in future_only.columns:
        future_only["temp_std"] = (
            pd.to_numeric(future_only["temp_std"], errors="coerce")
            .ffill()
            .fillna(0.0)
        )
    for col in [
        "temp_mean", "temp_min", "temp_max",
        "umi_mean", "umi_min", "vento_mean", "vento_max",
    ]:
        if col in future_only.columns:
            future_only[col] = pd.to_numeric(future_only[col], errors="coerce")

    X = builder.transform(future_only)
    if X.isna().any().any():
        X = X.fillna(0.0)

    impacto_pred = np.clip(model.predict(X), 0.0, None)

    impacto_agri_pred = impacto_pred * 0.5

    out = pd.DataFrame({
        "date": future_only["dia"].values,
        "impacto_total_previsto": impacto_pred,
        "impacto_agricultura_previsto": impacto_agri_pred,
        "temp_mean": future_only["temp_mean"].values,
        "temp_max": future_only["temp_max"].values,
        "umi_min": future_only["umi_min"].values,
        "vento_max": future_only["vento_max"].values,
        "cultura": farm_info["cultura"],
        "fazenda": farm_info["nome_fazenda"],
    })
    return out.sort_values("date").reset_index(drop=True)
