"""ETL Oracle -> DataFrame (farm x dia, com label binario).

Materializa a tabela-fato usada pelo modelo: uma linha por fazenda por dia,
com features climaticas agregadas e label `houve_evento` indicando se houve
algum desastre climatico severo registrado no CS_CLIMA_EVENTOS naquele dia
e naquele municipio.

Tambem expoe `load_climate_recent()` para inferencia: retorna o historico
recente (90 dias) de uma fazenda para que o pipeline de features calcule
as janelas rolantes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import oracledb
import pandas as pd
import streamlit as st

from .config import PROJECT_ROOT


_FORECAST_DAILY_FIELDS = ",".join([
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "relative_humidity_2m_mean",
    "relative_humidity_2m_min",
    "relative_humidity_2m_max",
    "wind_speed_10m_mean",
    "wind_speed_10m_max",
])


FACT_TABLE_QUERY = """
SELECT
    c.ID_FAZENDA,
    TRUNC(c.DATA_HORA) AS DIA,
    f.ESTADO,
    f.MUNICIPIO,
    f.CULTURA,
    f.TAMANHO,
    AVG(c.TEMPERATURA)       AS TEMP_MEAN,
    MIN(c.TEMPERATURA)       AS TEMP_MIN,
    MAX(c.TEMPERATURA)       AS TEMP_MAX,
    STDDEV(c.TEMPERATURA)    AS TEMP_STD,
    AVG(c.UMIDADE)           AS UMI_MEAN,
    MIN(c.UMIDADE)           AS UMI_MIN,
    AVG(c.VELOCIDADE_VENTO)  AS VENTO_MEAN,
    MAX(c.VELOCIDADE_VENTO)  AS VENTO_MAX,
    COUNT(*)                 AS N_LEITURAS,
    MAX(CASE WHEN EXISTS (
        SELECT 1 FROM CS_CLIMA_EVENTOS e
        WHERE e.UF       = f.ESTADO
          AND UPPER(e.MUNICIPIO) = UPPER(f.MUNICIPIO)
          AND TRUNC(e.REGISTRO)  = TRUNC(c.DATA_HORA)
    ) THEN 1 ELSE 0 END)     AS HOUVE_EVENTO
FROM CS_CLIMA c
JOIN CS_FAZENDAS f ON c.ID_FAZENDA = f.ID
GROUP BY c.ID_FAZENDA, TRUNC(c.DATA_HORA), f.ESTADO, f.MUNICIPIO, f.CULTURA, f.TAMANHO
ORDER BY c.ID_FAZENDA, TRUNC(c.DATA_HORA)
"""


FACT_TABLE_IMPACTO_QUERY = """
SELECT
    c.ID_FAZENDA,
    TRUNC(c.DATA_HORA) AS DIA,
    f.ESTADO,
    f.MUNICIPIO,
    f.CULTURA,
    f.TAMANHO,
    AVG(c.TEMPERATURA)       AS TEMP_MEAN,
    MIN(c.TEMPERATURA)       AS TEMP_MIN,
    MAX(c.TEMPERATURA)       AS TEMP_MAX,
    STDDEV(c.TEMPERATURA)    AS TEMP_STD,
    AVG(c.UMIDADE)           AS UMI_MEAN,
    MIN(c.UMIDADE)           AS UMI_MIN,
    AVG(c.VELOCIDADE_VENTO)  AS VENTO_MEAN,
    MAX(c.VELOCIDADE_VENTO)  AS VENTO_MAX,
    COUNT(*)                 AS N_LEITURAS,
    COALESCE(SUM(
        NVL(e.DM_17,0)+NVL(e.DM_18,0)+NVL(e.DM_19,0)+NVL(e.DM_20,0)+
        NVL(e.DM_21,0)+NVL(e.DM_22,0)+
        NVL(e.PEPL_1,0)+NVL(e.PEPL_2,0)+NVL(e.PEPL_3,0)+NVL(e.PEPL_4,0)+
        NVL(e.PEPL_S5,0)+NVL(e.PEPL_6,0)+NVL(e.PEPL_7,0)+NVL(e.PEPL_8,0)+
        NVL(e.PEPL_9,0)+NVL(e.PEPL_10,0)+NVL(e.PEPL_11,0)+
        NVL(e.PEPR_13,0)+NVL(e.PEPR_14,0)+NVL(e.PEPR_15,0)+NVL(e.PEPR_16,0)
    ), 0) AS IMPACTO_TOTAL,
    COALESCE(SUM(NVL(e.PEPR_AGRICULTURA, 0)), 0) AS IMPACTO_AGRICULTURA
FROM CS_CLIMA c
JOIN CS_FAZENDAS f ON c.ID_FAZENDA = f.ID
LEFT JOIN CS_CLIMA_EVENTOS e
    ON e.UF       = f.ESTADO
   AND UPPER(e.MUNICIPIO) = UPPER(f.MUNICIPIO)
   AND TRUNC(e.REGISTRO)  = TRUNC(c.DATA_HORA)
GROUP BY c.ID_FAZENDA, TRUNC(c.DATA_HORA), f.ESTADO, f.MUNICIPIO, f.CULTURA, f.TAMANHO
ORDER BY c.ID_FAZENDA, TRUNC(c.DATA_HORA)
"""


RECENT_CLIMATE_QUERY = """
SELECT
    c.ID_FAZENDA,
    TRUNC(c.DATA_HORA)       AS DIA,
    f.ESTADO,
    f.MUNICIPIO,
    f.CULTURA,
    f.TAMANHO,
    AVG(c.TEMPERATURA)       AS TEMP_MEAN,
    MIN(c.TEMPERATURA)       AS TEMP_MIN,
    MAX(c.TEMPERATURA)       AS TEMP_MAX,
    STDDEV(c.TEMPERATURA)    AS TEMP_STD,
    AVG(c.UMIDADE)           AS UMI_MEAN,
    MIN(c.UMIDADE)           AS UMI_MIN,
    AVG(c.VELOCIDADE_VENTO)  AS VENTO_MEAN,
    MAX(c.VELOCIDADE_VENTO)  AS VENTO_MAX
FROM CS_CLIMA c
JOIN CS_FAZENDAS f ON c.ID_FAZENDA = f.ID
WHERE c.DATA_HORA >= SYSDATE - :1
GROUP BY c.ID_FAZENDA, TRUNC(c.DATA_HORA), f.ESTADO, f.MUNICIPIO, f.CULTURA, f.TAMANHO
ORDER BY c.ID_FAZENDA, TRUNC(c.DATA_HORA)
"""


REGIAO_POR_UF = {
    "AC": "Norte", "AP": "Norte", "AM": "Norte", "PA": "Norte",
    "RO": "Norte", "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste",
    "PB": "Nordeste", "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste", "MS": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}


def _connect() -> oracledb.Connection:
    """Abre conexao Oracle. Tenta st.secrets primeiro, cai para variaveis de ambiente."""
    if hasattr(st, "secrets") and "ORACLE_USER" in st.secrets:
        user = st.secrets["ORACLE_USER"]
        pwd = st.secrets["ORACLE_PASSWORD"]
        dsn = st.secrets["ORACLE_DSN"]
    else:
        user = os.getenv("ORACLE_USER")
        pwd = os.getenv("ORACLE_PASSWORD")
        dsn = os.getenv("ORACLE_DSN")
        if not (user and pwd and dsn):
            raise RuntimeError(
                "Credenciais Oracle nao encontradas. "
                "Defina st.secrets (ORACLE_USER/ORACLE_PASSWORD/ORACLE_DSN) "
                "ou variaveis de ambiente."
            )
    return oracledb.connect(user=user, password=pwd, dsn=dsn)


@st.cache_data(ttl=3600, show_spinner="Carregando dados climaticos do Oracle...")
def load_dataset() -> pd.DataFrame:
    """Carrega a tabela-fato completa (10 fazendas x ~10 anos x 365 dias)."""
    conn = _connect()
    try:
        df = pd.read_sql(FACT_TABLE_QUERY, conn)
    finally:
        conn.close()

    df.columns = [c.lower() for c in df.columns]
    df["dia"] = pd.to_datetime(df["dia"])
    df["estado"] = df["estado"].str.upper()
    df["cultura"] = df["cultura"].fillna("Desconhecida").astype(str)
    df["tamanho"] = pd.to_numeric(df["tamanho"], errors="coerce").fillna(0)
    df["regiao"] = df["estado"].map(REGIAO_POR_UF).fillna("Desconhecida")

    for col in [
        "temp_mean", "temp_min", "temp_max", "temp_std",
        "umi_mean", "umi_min",
        "vento_mean", "vento_max",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["temp_std"] = df["temp_std"].fillna(0)
    df = df.sort_values(["id_fazenda", "dia"]).reset_index(drop=True)
    return df


@st.cache_data(ttl=3600, show_spinner="Carregando dados de impacto climatico do Oracle...")
def load_dataset_impacto() -> pd.DataFrame:
    """Carrega tabela-fato com IMPACTO_TOTAL (R$) e IMPACTO_AGRICULTURA (R$) como targets.

    Mesma granularidade (farm x dia) que `load_dataset()`, mas o target eh
    continuo (R$) em vez de binario (houve_evento). Usado para treinar o
    regressor Tweedie/Forest/Ridge que alimenta o scatter historico+projetado.
    """
    conn = _connect()
    try:
        df = pd.read_sql(FACT_TABLE_IMPACTO_QUERY, conn)
    finally:
        conn.close()

    df.columns = [c.lower() for c in df.columns]
    df["dia"] = pd.to_datetime(df["dia"])
    df["estado"] = df["estado"].str.upper()
    df["cultura"] = df["cultura"].fillna("Desconhecida").astype(str)
    df["tamanho"] = pd.to_numeric(df["tamanho"], errors="coerce").fillna(0)
    df["regiao"] = df["estado"].map(REGIAO_POR_UF).fillna("Desconhecida")

    for col in [
        "temp_mean", "temp_min", "temp_max", "temp_std",
        "umi_mean", "umi_min",
        "vento_mean", "vento_max",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["temp_std"] = df["temp_std"].fillna(0)
    df["impacto_total"] = (
        pd.to_numeric(df["impacto_total"], errors="coerce")
        .fillna(0)
        .clip(lower=0)
    )
    df["impacto_agricultura"] = (
        pd.to_numeric(df["impacto_agricultura"], errors="coerce")
        .fillna(0)
        .clip(lower=0)
    )
    df = df.sort_values(["id_fazenda", "dia"]).reset_index(drop=True)
    return df


@st.cache_data(ttl=300, show_spinner="Carregando historico recente da fazenda...")
def load_climate_recent(days_back: int = 90) -> pd.DataFrame:
    """Carrega ultimos N dias de clima para todas as fazendas (inferencia)."""
    conn = _connect()
    try:
        query = RECENT_CLIMATE_QUERY.replace(":1", str(int(days_back)))
        df = pd.read_sql(query, conn)
    finally:
        conn.close()

    df.columns = [c.lower() for c in df.columns]
    df["dia"] = pd.to_datetime(df["dia"])
    df["estado"] = df["estado"].str.upper()
    df["cultura"] = df["cultura"].fillna("Desconhecida").astype(str)
    df["tamanho"] = pd.to_numeric(df["tamanho"], errors="coerce").fillna(0)
    df["regiao"] = df["estado"].map(REGIAO_POR_UF).fillna("Desconhecida")
    for col in [
        "temp_mean", "temp_min", "temp_max", "temp_std",
        "umi_mean", "umi_min",
        "vento_mean", "vento_max",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["temp_std"] = df["temp_std"].fillna(0)
    df = df.sort_values(["id_fazenda", "dia"]).reset_index(drop=True)
    return df


def train_test_split_temporal(
    df: pd.DataFrame, split_date: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split temporal: treino = antes de split_date, teste = depois."""
    cutoff = pd.Timestamp(split_date)
    train = df[df["dia"] < cutoff].copy()
    test = df[df["dia"] >= cutoff].copy()
    return train, test


def save_dataset_cache(df: pd.DataFrame, name: str = "fact_table.parquet") -> Path:
    """Salva dataset local em parquet (para treino offline / testes)."""
    cache_dir = PROJECT_ROOT / "data" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / name
    df.to_parquet(path, index=False)
    return path


def load_dataset_cache(name: str = "fact_table.parquet") -> Optional[pd.DataFrame]:
    """Carrega dataset do cache parquet, se existir."""
    path = PROJECT_ROOT / "data" / "cache" / name
    if not path.exists():
        return None
    return pd.read_parquet(path)


@st.cache_data(ttl=3600, show_spinner="Buscando fazendas no município...")
def lookup_farms_in_municipio(uf: str, municipio: str) -> list[dict]:
    """Retorna lista de fazendas em (uf, municipio).

    Cada dict contem:
        id_fazenda, nome_fazenda, latitude, longitude, cultura, tamanho, regiao
    Retorna lista vazia se nenhuma fazenda estiver cadastrada.
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ID, NOME_FAZENDA, LATITUDE, LONGITUDE, CULTURA, TAMANHO
            FROM CS_FAZENDAS
            WHERE UPPER(ESTADO) = :1 AND UPPER(MUNICIPIO) = :2
              AND LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL
            ORDER BY ID
            """,
            [uf.upper(), municipio.upper()],
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    farms = []
    for r in rows:
        farms.append({
            "id_fazenda": int(r[0]),
            "nome_fazenda": str(r[1]),
            "latitude": float(r[2]),
            "longitude": float(r[3]),
            "cultura": str(r[4]) if r[4] is not None else "Desconhecida",
            "tamanho": float(r[5]) if r[5] is not None else 0.0,
            "regiao": REGIAO_POR_UF.get(uf.upper(), "Desconhecida"),
        })
    return farms


def _aggregate_farms(farms: list[dict]) -> dict:
    """Agrega lista de fazendas em 1 (median lat/lon, mode cultura, mean tamanho)."""
    if not farms:
        raise ValueError("Lista de fazendas vazia")
    if len(farms) == 1:
        f = farms[0]
        return {
            "id_fazenda": f["id_fazenda"],
            "nome_fazenda": f["nome_fazenda"],
            "latitude": f["latitude"],
            "longitude": f["longitude"],
            "cultura": f["cultura"],
            "tamanho": f["tamanho"],
            "regiao": f["regiao"],
        }

    import statistics
    lats = [f["latitude"] for f in farms]
    lons = [f["longitude"] for f in farms]
    tamanhos = [f["tamanho"] for f in farms]
    culturas = [f["cultura"] for f in farms]
    try:
        cultura_mode = statistics.mode(culturas)
    except statistics.StatisticsError:
        cultura_mode = culturas[0]

    return {
        "id_fazenda": farms[0]["id_fazenda"],
        "nome_fazenda": " + ".join(f["nome_fazenda"] for f in farms),
        "latitude": statistics.median(lats),
        "longitude": statistics.median(lons),
        "cultura": cultura_mode,
        "tamanho": statistics.mean(tamanhos),
        "regiao": farms[0]["regiao"],
    }


@st.cache_data(ttl=3600, show_spinner="Buscando previsao Open-Meteo (14 dias)...")
def fetch_open_meteo_forecast(
    lat: float, lon: float, days: int = 14
) -> pd.DataFrame:
    """Busca previsao climatica diaria (ate 16 dias) via Open-Meteo.

    Retorna DataFrame com mesmas colunas da tabela-fato (exceto label):
        dia, temp_mean, temp_min, temp_max, temp_std,
        umi_mean, umi_min, vento_mean, vento_max
    """
    import requests

    days = max(1, min(int(days), 16))
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "daily": _FORECAST_DAILY_FIELDS,
        "timezone": "America/Sao_Paulo",
        "forecast_days": days,
    }
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()
    daily = payload.get("daily") or {}
    if not daily or "time" not in daily or not daily["time"]:
        return pd.DataFrame()

    df = pd.DataFrame({
        "dia": pd.to_datetime(daily["time"]),
        "temp_mean": daily.get("temperature_2m_mean"),
        "temp_min": daily.get("temperature_2m_min"),
        "temp_max": daily.get("temperature_2m_max"),
        "umi_mean": daily.get("relative_humidity_2m_mean"),
        "umi_min": daily.get("relative_humidity_2m_min"),
        "vento_mean": daily.get("wind_speed_10m_mean"),
        "vento_max": daily.get("wind_speed_10m_max"),
    })
    for col in [
        "temp_mean", "temp_min", "temp_max",
        "umi_mean", "umi_min",
        "vento_mean", "vento_max",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["temp_std"] = ((df["temp_max"] - df["temp_min"]) / 4.0).clip(lower=0).fillna(0)
    return df


def attach_farm_metadata(
    forecast_df: pd.DataFrame, farm_info: dict
) -> pd.DataFrame:
    """Anexa metadados de uma fazenda (ou fazenda agregada) no forecast."""
    if forecast_df.empty:
        return forecast_df
    out = forecast_df.copy()
    out["id_fazenda"] = farm_info["id_fazenda"]
    out["estado"] = farm_info.get("estado", "")
    out["municipio"] = farm_info.get("municipio", "")
    out["cultura"] = farm_info["cultura"]
    out["tamanho"] = farm_info["tamanho"]
    out["regiao"] = farm_info["regiao"]
    return out
