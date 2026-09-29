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

# Padronização (2026-09-28, alinhada com os NPL 1, 2 e 3 da Nádia):
# - só entram orientações dos três NPLs; limiares de sensor (LIMIAR_OPERACIONAL_SENSOR, ex.: marcha lenta,
#   carga do motor, nível de combustível) não são manutenção nem alerta climático e saíram;
# - uma lista única de subsistemas para os três NPLs;
# - unidades por extenso (HORAS, KM, DIAS, CELSIUS, KM_H...).
TIPO_ORIENTACAO = (
    "MANUTENCAO_PROGRAMADA",   # NPL 1
    "ALERTA_TEMP_MINIMA",      # NPL 2 (frio)
    "ALERTA_TEMP_MAXIMA",      # NPL 2 (calor)
    "RISCO_CHUVA_DESLIZE",     # NPL 3
)

METRICA_GATILHO = (
    "HORIMETRO",
    "CALENDARIO",
    "HODOMETRO",
    "CONDICIONAL",
    "TEMPERATURA_AMBIENTE",
    "VELOCIDADE_KMH",
    "UMIDADE_SOLO",
    "INCLINACAO",
    "CONDICAO_CLIMATICA",
)

UNIDADE_MEDIDA = (
    "HORAS",
    "KM",
    "DIAS",
    "CELSIUS",
    "KM_H",
    "PERCENTUAL",
    "GRAUS",
)

# Unidade padrão de cada métrica (quando o gatilho tem valor)
UNIDADE_DA_METRICA = {
    "HORIMETRO": "HORAS",
    "HODOMETRO": "KM",
    "CALENDARIO": "DIAS",
    "TEMPERATURA_AMBIENTE": "CELSIUS",
    "VELOCIDADE_KMH": "KM_H",
    "UMIDADE_SOLO": "PERCENTUAL",
}

SUBSISTEMA = (
    "MOTOR",
    "ARREFECIMENTO",
    "COMBUSTIVEL",
    "TRANSMISSAO",
    "REDUCOES_TREM_ACIONAMENTO",
    "HIDRAULICO",
    "CORTE_PROCESSAMENTO",
    "RODANTE_PNEUS",
    "FREIOS",
    "ELETRICO",
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
    "FAZER_RODIZIO",
    "TRAVAR_PEDAIS_FREIO",
    "ENGATAR_TDA",
    "REDUZIR_VELOCIDADE",
    "EVITAR_OPERACAO",
    "TRATAR_COMBUSTIVEL",
    "PRE_AQUECER",
    "AQUECER_MOTOR",
    "PARAR_OPERACAO",
)

IDIOMA = ("PT", "EN")

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
    idioma_origem: str = "PT"

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
        _validar("idioma_origem", self.idioma_origem, IDIOMA, obrigatorio=True)
        if self.metrica_gatilho is not None:
            _validar("metrica_gatilho", self.metrica_gatilho, METRICA_GATILHO, obrigatorio=False)
        if self.unidade_medida is not None:
            _validar("unidade_medida", self.unidade_medida, UNIDADE_MEDIDA, obrigatorio=False)
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
