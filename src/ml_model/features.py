"""Engenharia de features para o modelo preditivo.

Recebe o DataFrame bruto do `data.load_dataset()` (1 linha = 1 fazenda x 1 dia)
e adiciona:

- Indices climaticos derivados (heat_index, faixa_termica)
- Sazonalidade ciclica (dia_sin, dia_cos)
- Janelas rolantes (3d, 7d, 30d) por fazenda
- Anomalia de temperatura vs media historica sazonal
- Dias secos consecutivos (run-length de umi_min < 20%)

A classe `FeatureBuilder` eh stateless depois do `fit()` e pode ser pickled
junto com o modelo (joblib). Em producao, basta chamar `transform()` em
uma janela de ~90 dias para a fazenda-alvo.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .config import (
    FEATURE_COLUMNS_CATEGORICAL,
    FEATURE_COLUMNS_CYCLIC,
    FEATURE_COLUMNS_NUMERIC,
    TARGET_COLUMN,
)


class FeatureBuilder:
    """Pipeline de transformacao de dados brutos -> matriz de features."""

    def __init__(self) -> None:
        self.feature_names_: list[str] = []
        self.cultura_classes_: list[str] = []
        self.regiao_classes_: list[str] = []
        self.mes_media_temp_: Optional[pd.Series] = None

    def fit(self, df: pd.DataFrame) -> "FeatureBuilder":
        """Aprende vocabularios (culturas, regioes) e medias sazonais."""
        self.cultura_classes_ = sorted(df["cultura"].dropna().unique().tolist())
        self.regiao_classes_ = sorted(df["regiao"].dropna().unique().tolist())

        climatology = (
            df.assign(mes=df["dia"].dt.month)
            .groupby(["regiao", "mes"])["temp_mean"]
            .mean()
        )
        self.mes_media_temp_ = climatology

        cultura_features = [f"cultura__{c}" for c in self.cultura_classes_]
        regiao_features = [f"regiao__{r}" for r in self.regiao_classes_]
        self.feature_names_ = (
            list(FEATURE_COLUMNS_NUMERIC)
            + list(FEATURE_COLUMNS_CYCLIC)
            + cultura_features
            + regiao_features
        )
        return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aplica transformacoes. Retorna DataFrame com colunas = feature_names_."""
        out = df.copy()
        out = self._add_derived_indices(out)
        out = self._add_rolling_windows(out)
        out = self._add_dias_secos(out)
        out = self._add_sazonalidade(out)
        out = self._add_anomalia_temp(out)
        out = self._one_hot_categoricas(out)
        return out[self.feature_names_]

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.fit(df).transform(df)

    @staticmethod
    def _add_derived_indices(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["heat_index"] = df["temp_mean"] + 0.5 * (df["umi_mean"] / 100.0) * (df["temp_mean"] - 14.5)
        df["faixa_termica"] = df["temp_max"] - df["temp_min"]
        return df

    @staticmethod
    def _add_rolling_windows(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["id_fazenda", "dia"])
        grouped = df.groupby("id_fazenda", group_keys=False)

        df["temp_mean_3d"] = grouped["temp_mean"].transform(
            lambda s: s.rolling(3, min_periods=1).mean()
        )
        df["temp_mean_7d"] = grouped["temp_mean"].transform(
            lambda s: s.rolling(7, min_periods=1).mean()
        )
        df["temp_mean_30d"] = grouped["temp_mean"].transform(
            lambda s: s.rolling(30, min_periods=1).mean()
        )
        df["umi_min_7d"] = grouped["umi_min"].transform(
            lambda s: s.rolling(7, min_periods=1).min()
        )
        df["umi_min_30d"] = grouped["umi_min"].transform(
            lambda s: s.rolling(30, min_periods=1).min()
        )
        df["vento_max_7d"] = grouped["vento_max"].transform(
            lambda s: s.rolling(7, min_periods=1).max()
        )
        df["vento_max_30d"] = grouped["vento_max"].transform(
            lambda s: s.rolling(30, min_periods=1).max()
        )
        return df

    @staticmethod
    def _add_dias_secos(df: pd.DataFrame) -> pd.DataFrame:
        """Conta dias consecutivos com umi_min < 20% por fazenda."""
        df = df.copy()
        df = df.sort_values(["id_fazenda", "dia"])
        seco = (df["umi_min"] < 20.0).astype(int)

        def run_length(series: pd.Series) -> pd.Series:
            counts = []
            c = 0
            for v in series:
                if v == 1:
                    c += 1
                else:
                    c = 0
                counts.append(c)
            return pd.Series(counts, index=series.index)

        df["dias_secos_consecutivos"] = (
            df.groupby("id_fazenda", group_keys=False)["umi_min"]
            .transform(lambda s: run_length((s < 20.0).astype(int)))
        )
        return df

    def _add_sazonalidade(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        doy = df["dia"].dt.dayofyear
        df["dia_sin"] = np.sin(2 * np.pi * doy / 365.0)
        df["dia_cos"] = np.cos(2 * np.pi * doy / 365.0)
        return df

    def _add_anomalia_temp(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if self.mes_media_temp_ is None:
            df["anomalia_temp"] = 0.0
            return df
        climatology = self.mes_media_temp_
        df["mes"] = df["dia"].dt.month
        lookup = df.set_index(["regiao", "mes"]).index.map(climatology)
        lookup = pd.Series(lookup, index=df.index).astype(float)
        df["anomalia_temp"] = df["temp_mean"] - lookup
        df = df.drop(columns=["mes"])
        return df

    def _one_hot_categoricas(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for c in self.cultura_classes_:
            df[f"cultura__{c}"] = (df["cultura"] == c).astype(int)
        for r in self.regiao_classes_:
            df[f"regiao__{r}"] = (df["regiao"] == r).astype(int)
        return df


def get_feature_matrix_and_target(
    df: pd.DataFrame, builder: FeatureBuilder
) -> tuple[pd.DataFrame, pd.Series]:
    """Atalho: retorna X, y a partir do DataFrame bruto e do builder ja fitado."""
    X = builder.transform(df)
    y = df[TARGET_COLUMN].astype(int)
    return X, y
