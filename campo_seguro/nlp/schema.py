"""Schema canônico do dataset interno de orientações (Fase 1, determinística).

18 campos: os 11 reais de CS_EQUIPAMENTOS_ORIENTACOES (Oracle, criada pela
Nádia) + nome_documento_origem/data_publicacao_manual/link_manual/idioma_origem
(hoje sem tabela auxiliar de manuais definida) + pagina_origem/criticidade/
sinal_seguranca/sensor_iot (usados no score §6 e nos alertas, mas ainda não
confirmados como colunas na tabela real — ver docs/memoria.md §5 e §10 item 12).

`export.py` é quem decide, na hora de gerar o .sql, quais desses 18 campos
realmente viram coluna na tabela do Oracle.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional

TIPO_ORIENTACAO = (
    "MANUTENCAO_PROGRAMADA",
    "ALERTA_TEMP_MINIMA",
    "ALERTA_TEMP_MAXIMA",
    "RISCO_CHUVA_DESLIZE",
    "LIMIAR_OPERACIONAL_SENSOR",
)

METRICA_GATILHO = (
    "HORIMETRO",
    "CALENDARIO",
    "HODOMETRO",
    "CONDICIONAL",
    "TEMPERATURA_AMBIENTE",
    "TEMPERATURA_MOTOR",
    "TEMPERATURA_HIDRAULICO",
    "CARGA_MOTOR",
    "VELOCIDADE_KMH",
    "UMIDADE_SOLO",
    "CONDICAO_CLIMATICA",
    # extensões da Fase 1 para cobrir os limiares de sensor do kickoff (item
    # 1.2) que não tinham métrica correspondente na lista original (memória §5):
    "NIVEL_COMBUSTIVEL",
    "NIVEL_DEF",
    "PRESSAO_CORTADOR_BASE",
    "TEMPO_MARCHA_LENTA",
    "ALTITUDE",
)

SUBSISTEMA = (
    "MOTOR",
    "COMBUSTIVEL",
    "ARREFECIMENTO",
    "TRANSMISSAO",
    "TREM_ACIONAMENTO",
    "HIDRAULICO",
    "FREIOS",
    "RODANTE_PNEUS",
    "ELETRICO",
    "CORTE_PROCESSAMENTO",
    "CABINE_ESTRUTURA",
    "CHASSI",
)

ACAO_TECNICA = (
    "VERIFICAR",
    "INSPECIONAR",
    "LIMPAR",
    "LUBRIFICAR",
    "TROCAR",
    "SUBSTITUIR",
    "DRENAR",
    "AJUSTAR",
    "APERTAR",
    "TESTAR",
    "LAVAR",
    "CALIBRAR",
    "TRAVAR_PEDAIS_FREIO",
    "ENGATAR_TDA",
    "REDUZIR_VELOCIDADE",
    "TRATAR_COMBUSTIVEL",
    "AQUECER_MOTOR",
    "PARAR_OPERACAO",
)

CRITICIDADE = ("BAIXA", "MEDIA", "ALTA", "CRITICA")

SINAL_SEGURANCA = ("PERIGO", "ATENCAO", "CUIDADO", "IMPORTANTE")

SENSOR_IOT = (
    "TEMP_MOTOR",
    "TEMP_HIDRAULICO",
    "CARGA_MOTOR",
    "HORIMETRO",
    "GPS",
    "ACELEROMETRO",
    "TEMP_AMBIENTE",
    "UMIDADE",
)

# Colunas reais de CS_EQUIPAMENTOS_ORIENTACOES no Oracle FIAP (print do SQL
# Developer, 2026-09-24). Usado por export.py para gerar o .sql sem inventar
# colunas que não existem na tabela da Nádia.
COLUNAS_ORACLE_REAIS = (
    "cod_fonte_manual",
    "tipo_orientacao",
    "subsistema",
    "acao_tecnica",
    "detalhamento_orientacao",
    "metrica_gatilho",
    "valor_gatilho",
    "unidade_medida",
    "fator_condicional",
    "texto_bruto_original",
)
COLUNAS_ORACLE_NOT_NULL = (
    "cod_fonte_manual",
    "tipo_orientacao",
    "subsistema",
    "acao_tecnica",
)


def _validar(nome_campo: str, valor: Optional[str], enum: tuple[str, ...], obrigatorio: bool) -> None:
    if valor is None:
        if obrigatorio:
            raise ValueError(f"{nome_campo} é obrigatório e veio None")
        return
    if valor not in enum:
        raise ValueError(f"{nome_campo}={valor!r} não está no enum {enum}")


@dataclass
class Orientacao:
    # --- rastreabilidade do manual ---
    cod_fonte_manual: str
    nome_documento_origem: Optional[str] = None
    data_publicacao_manual: Optional[str] = None
    link_manual: Optional[str] = None
    idioma_origem: str = "pt"

    # --- classificação ---
    tipo_orientacao: str = "MANUTENCAO_PROGRAMADA"
    subsistema: str = "CHASSI"
    acao_tecnica: str = "VERIFICAR"

    # --- conteúdo ---
    detalhamento_orientacao: str = ""
    metrica_gatilho: Optional[str] = None
    valor_gatilho: Optional[float] = None
    unidade_medida: Optional[str] = None
    fator_condicional: Optional[str] = None
    texto_bruto_original: str = ""

    # --- rastreabilidade + score/alerta (podem não existir ainda no Oracle) ---
    pagina_origem: Optional[int] = None
    criticidade: str = "MEDIA"
    sinal_seguranca: Optional[str] = None
    sensor_iot: Optional[str] = None

    # --- internos (nunca exportados para a Nádia/Oracle) ---
    origem_extracao: str = "PARSER_DETERMINISTICO"
    qualidade: str = "OK"

    def validar(self) -> None:
        _validar("tipo_orientacao", self.tipo_orientacao, TIPO_ORIENTACAO, obrigatorio=True)
        _validar("subsistema", self.subsistema, SUBSISTEMA, obrigatorio=True)
        _validar("acao_tecnica", self.acao_tecnica, ACAO_TECNICA, obrigatorio=True)
        _validar("criticidade", self.criticidade, CRITICIDADE, obrigatorio=True)
        if self.metrica_gatilho is not None:
            _validar("metrica_gatilho", self.metrica_gatilho, METRICA_GATILHO, obrigatorio=False)
        if self.sinal_seguranca is not None:
            _validar("sinal_seguranca", self.sinal_seguranca, SINAL_SEGURANCA, obrigatorio=False)
        if self.sensor_iot is not None:
            _validar("sensor_iot", self.sensor_iot, SENSOR_IOT, obrigatorio=False)
        if not self.cod_fonte_manual:
            raise ValueError("cod_fonte_manual é obrigatório")
        if not self.texto_bruto_original:
            raise ValueError("texto_bruto_original é obrigatório (rastreabilidade)")

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


CAMPOS_DATASET_INTERNO = tuple(f.name for f in fields(Orientacao) if f.name not in ("origem_extracao", "qualidade"))
