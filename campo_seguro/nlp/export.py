"""Exportação da Fase 1: junta parsers + limiares e gera outputs/orientacoes/.

Saídas (kickoff item 1.4):
- CS_EQUIPAMENTOS_ORIENTACOES.csv (utf-8-sig, ';') e .json — dataset interno,
  18 campos (schema.CAMPOS_DATASET_INTERNO).
- CS_EQUIPAMENTOS_ORIENTACOES.xlsx — abas ORIENTACOES, RESUMO_INTERVALOS,
  RESUMO_SUBSISTEMA, LIMIARES_SENSOR, DICIONARIO, PENDENCIAS.
- CS_EQUIPAMENTOS_ORIENTACOES.sql — só INSERTs, só as 11 colunas reais de
  CS_EQUIPAMENTOS_ORIENTACOES no Oracle (sem DDL novo — a tabela já existe,
  ver docs/memoria.md §5 e §7).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from campo_seguro.nlp import schema as sch
from campo_seguro.nlp.extract_thresholds import extrair_limiares_ch950, extrair_limiares_5060e
from campo_seguro.nlp.parse_interval_tables import parse_ch950, parse_5060e
from campo_seguro.nlp.schema import COLUNAS_ORACLE_REAIS, Orientacao

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("outputs/orientacoes")

# limite real da coluna FATOR_CONDICIONAL (VARCHAR2(255 BYTE)) no Oracle —
# algumas notas de rodapé combinadas (5060E) passam disso; truncamos só no
# .sql e registramos em PENDENCIAS (o dataset interno CSV/JSON/XLSX guarda o
# texto completo).
LIMITE_BYTES_FATOR_CONDICIONAL = 255


def gerar_todas_orientacoes() -> tuple[list[Orientacao], list[str]]:
    """Roda os 2 parsers + os 2 extratores de limiar. Retorna (orientações, pendências)."""
    pendencias: list[str] = []
    todas: list[Orientacao] = []

    todas += parse_ch950("data/raw/manual_colhedora.pdf")
    todas += parse_5060e("data/raw/manual_trator_5060e.pdf")
    todas += extrair_limiares_ch950("data/raw/manual_colhedora.pdf")
    todas += extrair_limiares_5060e("data/raw/manual_trator_5060e.pdf")

    for o in todas:
        o.validar()

    pendencias.append(
        "5060E, seção 'A Cada 400 Horas de Operação' / Rodas e Peças de Fixação: as 3 tarefas "
        "'Verifique os rolamentos/pino pivô/pivô da direção do eixo dianteiro' vieram sem ponto final "
        "na fonte (só a nota 'f' colada) e por segurança do parser (evitar engolir o próximo cabeçalho) "
        "ficaram combinadas em 1 linha só, em vez de 3. Revisar manualmente se quiser separar."
    )
    pendencias.append(
        "Limiares de DEF (congela a -11 °C, degrada acima de 30 °C) e diesel abaixo de -10 °C/-20 °C "
        "citados na memória (§4) não foram localizados com página confirmada no 5060E (seção 205-x não "
        "tem esse trecho explícito nas páginas revisadas) — não incluídos em LIMIARES_SENSOR para não "
        "inventar página de origem. Falta investigar se estão em outra seção do manual."
    )
    pendencias.append(
        "Manual Mahindra 6075: sem arquivo real disponível (só um placeholder vazio já removido do repo)."
    )
    pendencias.append(
        "cod_fonte_manual JD-CH950-OMCXT31163-B3 / JD-5060E-OMTR132548-E6: nome_documento_origem, "
        "data_publicacao_manual, link_manual e idioma_origem estão preenchidos com o que sabemos, mas a "
        "Nádia comentou que vai criar uma tabela auxiliar de manuais separada — precisa alinhar se esses "
        "4 campos continuam aqui ou migram para lá."
    )
    pendencias.append(
        "pagina_origem, criticidade, sinal_seguranca, sensor_iot: não existem como coluna em "
        "CS_EQUIPAMENTOS_ORIENTACOES no Oracle hoje (ver §5) — mantidos só no dataset interno "
        "(CSV/JSON/XLSX). O .sql não inclui essas colunas."
    )
    pendencias.append(
        "'Complexidade da manutenção' (peso 0,25 da régua) é descrita como 'recomendações em 5 anos', "
        "mas o que geramos aqui é a contagem estática de itens do manual (226 no total). Precisa alinhar "
        "com a Nádia como converter um pelo outro antes de usar isso no score."
    )
    return todas, pendencias


def _dataframe_interno(orientacoes: list[Orientacao]) -> pd.DataFrame:
    linhas = [{campo: getattr(o, campo) for campo in sch.CAMPOS_DATASET_INTERNO} for o in orientacoes]
    return pd.DataFrame(linhas, columns=list(sch.CAMPOS_DATASET_INTERNO))


def exportar_csv_json(orientacoes: list[Orientacao]) -> None:
    df = _dataframe_interno(orientacoes)
    df.to_csv(OUTPUT_DIR / "CS_EQUIPAMENTOS_ORIENTACOES.csv", index=False, sep=";", encoding="utf-8-sig")
    with open(OUTPUT_DIR / "CS_EQUIPAMENTOS_ORIENTACOES.json", "w", encoding="utf-8") as f:
        json.dump(df.to_dict(orient="records"), f, ensure_ascii=False, indent=2)


def _resumo_intervalos(df: pd.DataFrame) -> pd.DataFrame:
    mp = df[df["tipo_orientacao"] == "MANUTENCAO_PROGRAMADA"].copy()
    mp["faixa"] = mp.apply(
        lambda r: f"{r['metrica_gatilho']}={r['valor_gatilho']}" + (f" [{r['fator_condicional']}]" if r["fator_condicional"] else ""),
        axis=1,
    )
    return mp.groupby(["cod_fonte_manual", "faixa"]).size().reset_index(name="qtd_itens").sort_values(["cod_fonte_manual", "qtd_itens"], ascending=[True, False])


def _resumo_subsistema(df: pd.DataFrame) -> pd.DataFrame:
    return df.groupby(["cod_fonte_manual", "subsistema"]).size().reset_index(name="qtd_itens").sort_values(["cod_fonte_manual", "qtd_itens"], ascending=[True, False])


def _dicionario() -> pd.DataFrame:
    descricoes = {
        "cod_fonte_manual": ("Identificador do manual fonte", "texto livre, ex. JD-CH950-OMCXT31163-B3"),
        "nome_documento_origem": ("Nome legível do documento", "texto livre"),
        "data_publicacao_manual": ("Data de publicação/edição do manual", "null até confirmação (não inventado)"),
        "link_manual": ("Link para o PDF do manual", "null até confirmação"),
        "idioma_origem": ("Idioma do texto original", "pt | en"),
        "tipo_orientacao": ("Tipo da orientação", " | ".join(sch.TIPO_ORIENTACAO)),
        "subsistema": ("Subsistema do equipamento afetado", " | ".join(sch.SUBSISTEMA)),
        "acao_tecnica": ("Verbo de ação canônico", " | ".join(sch.ACAO_TECNICA)),
        "detalhamento_orientacao": ("Frase pronta para exibir como alerta", "texto livre"),
        "metrica_gatilho": ("O que dispara a orientação", " | ".join(sch.METRICA_GATILHO)),
        "valor_gatilho": ("Valor numérico do gatilho", "número (horas, °C, %, bar, m, min — ver unidade_medida)"),
        "unidade_medida": ("Unidade de valor_gatilho", "h | °C | % | bar | m | min"),
        "fator_condicional": ("Condição adicional (equivalente de calendário, amaciamento, nota de rodapé)", "texto estruturado, ex. OU_CALENDARIO:DIARIO"),
        "texto_bruto_original": ("Texto exatamente como extraído do manual (rastreabilidade)", "texto livre"),
        "pagina_origem": ("Página do PDF onde a orientação foi encontrada", "inteiro — NÃO existe como coluna no Oracle hoje, só no dataset interno"),
        "criticidade": ("Nível de criticidade", " | ".join(sch.CRITICIDADE) + " — NÃO existe como coluna no Oracle hoje"),
        "sinal_seguranca": ("Sinal de segurança do manual (PERIGO/ATENÇÃO/...), quando aplicável", " | ".join(sch.SINAL_SEGURANCA) + " | null — só Fase 2 (prosa); NÃO existe como coluna no Oracle hoje"),
        "sensor_iot": ("Sensor IoT associado ao limiar, quando aplicável", " | ".join(sch.SENSOR_IOT) + " | null — NÃO existe como coluna no Oracle hoje"),
    }
    return pd.DataFrame(
        [{"coluna": k, "descricao": v[0], "valores_possiveis": v[1]} for k, v in descricoes.items()]
    )


def exportar_xlsx(orientacoes: list[Orientacao], pendencias: list[str]) -> None:
    df = _dataframe_interno(orientacoes)
    with pd.ExcelWriter(OUTPUT_DIR / "CS_EQUIPAMENTOS_ORIENTACOES.xlsx", engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="ORIENTACOES", index=False)
        _resumo_intervalos(df).to_excel(writer, sheet_name="RESUMO_INTERVALOS", index=False)
        _resumo_subsistema(df).to_excel(writer, sheet_name="RESUMO_SUBSISTEMA", index=False)
        df[df["tipo_orientacao"] == "LIMIAR_OPERACIONAL_SENSOR"].to_excel(writer, sheet_name="LIMIARES_SENSOR", index=False)
        _dicionario().to_excel(writer, sheet_name="DICIONARIO", index=False)
        pd.DataFrame({"pendencia": pendencias}).to_excel(writer, sheet_name="PENDENCIAS", index=False)


def _sql_str(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "NULL"
    if isinstance(valor, (int, float)):
        return repr(float(valor))
    texto = str(valor).replace("'", "''")
    return f"'{texto}'"


def exportar_sql(orientacoes: list[Orientacao], pendencias: list[str]) -> None:
    linhas_sql = [
        "-- INSERTs para CS_EQUIPAMENTOS_ORIENTACOES (tabela já existe no Oracle FIAP, criada pela Nádia).",
        "-- Gerado por campo_seguro/nlp/export.py — só as 11 colunas reais (ver docs/memoria.md §5).",
        "-- ID é preenchido pela sequence da própria tabela, não incluído aqui.",
        "",
    ]
    colunas_sql = ", ".join(c.upper() for c in COLUNAS_ORACLE_REAIS)
    for o in orientacoes:
        d = o.as_dict()
        fator = d["fator_condicional"]
        if fator and len(fator.encode("utf-8")) > LIMITE_BYTES_FATOR_CONDICIONAL:
            truncado = fator.encode("utf-8")[: LIMITE_BYTES_FATOR_CONDICIONAL - 1].decode("utf-8", errors="ignore")
            pendencias.append(
                f"fator_condicional truncado no .sql (>{LIMITE_BYTES_FATOR_CONDICIONAL} bytes, coluna "
                f"VARCHAR2(255 BYTE)) para '{d['cod_fonte_manual']}' / '{d['detalhamento_orientacao'][:60]}...' "
                f"— texto completo preservado no CSV/JSON/XLSX."
            )
            fator = truncado
        valores = [_sql_str(d[c]) for c in COLUNAS_ORACLE_REAIS if c != "fator_condicional"]
        # fator_condicional é tratado à parte por causa do truncamento acima
        idx_fator = list(COLUNAS_ORACLE_REAIS).index("fator_condicional")
        valores.insert(idx_fator, _sql_str(fator))
        linhas_sql.append(f"INSERT INTO CS_EQUIPAMENTOS_ORIENTACOES ({colunas_sql}) VALUES ({', '.join(valores)});")
    linhas_sql.append("COMMIT;")
    (OUTPUT_DIR / "CS_EQUIPAMENTOS_ORIENTACOES.sql").write_text("\n".join(linhas_sql), encoding="utf-8")


def executar() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    orientacoes, pendencias = gerar_todas_orientacoes()
    logger.info("Total de orientações geradas: %d", len(orientacoes))
    exportar_csv_json(orientacoes)
    exportar_sql(orientacoes, pendencias)  # pode adicionar pendências (truncamento) — roda antes do xlsx
    exportar_xlsx(orientacoes, pendencias)
    logger.info("Arquivos gravados em %s", OUTPUT_DIR)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    executar()
