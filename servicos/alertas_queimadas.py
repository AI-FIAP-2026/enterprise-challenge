# -*- coding: utf-8 -*-
"""
Focos de queimada do INPE -> CS_ALERTAS

Serviço INCREMENTAL, executado pelo pipeline. Busca só o período recente:
  Fase 1 - Diário (desde a última execução com sucesso até hoje) ... .../diario/Brasil/
  Fase 2 - 10 minutos (depois do último foco confirmado até agora) ... .../10min/

O HISTÓRICO (arquivos mensais desde 01/2023) é carregado uma única vez, à mão, pelo script
tratamento/queimadas_historico.py, que reaproveita as funções deste arquivo.
Os arquivos anuais do INPE não são usados: não trazem risco de fogo nem satélite.

Regras de gravação (valem para todas as fases):
  - Cada foco tem uma CHAVE_FOCO única (satélite + data/hora UTC + coordenadas), igual em qualquer arquivo.
  - Foco novo com risco de fogo ACIMA de RISCO_MINIMO (40%) é inserido.
  - Foco já gravado só é alterado se alguma informação mudou (risco, município, fazenda próxima...).
  - Foco já gravado cujo risco passou a ficar abaixo do mínimo (ou -999) é removido.
  - O arquivo de 10 minutos não traz risco de fogo: o foco entra como "Aguardando risco" e é confirmado
    (ou removido) quando o arquivo diário com o risco for lido.
"""
import sys
import os

# --- AJUSTE DE DIRETÓRIO PARA O PIPELINE E EXECUÇÃO ISOLADA ---
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)
# -------------------------------------------------------------

import re
import io
import csv
import math
import time
import logging
import zipfile
import tempfile
import datetime
import requests
from concurrent.futures import ThreadPoolExecutor
import oracledb
from auth import USER, PASSWORD, DSN

# Mensagens vão para o console e, quando executado pelo pipeline, também para logs/pipeline.log
logger = logging.getLogger(__name__)

URL_BASE = "https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/"
URL_MENSAL = URL_BASE + "mensal/Brasil/"
URL_DIARIO = URL_BASE + "diario/Brasil/"
URL_10MIN = URL_BASE + "10min/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
ORIGEM = "INPE"

# Quantos dias para trás o diário relê a partir da última execução com sucesso (o INPE ainda
# completa o arquivo do dia depois que ele vira) e o limite máximo de dias lidos numa execução.
# Períodos mais antigos que DIAS_MAX_DIARIO são carregados pelo tratamento/queimadas_historico.py.
DIAS_RELEITURA_DIARIO = 1
DIAS_MAX_DIARIO = 30

# --- FILTROS -----------------------------------------------------------------------------------
# Risco de fogo mínimo (0 a 1). 0.40 = 40%. None grava todos os focos.
# Só entram focos ACIMA de 40%: na classificação do INPE, até 40% é "Baixo" (0,40 inclusive),
# então a partir daqui só ficam os níveis Médio, Alto e Crítico.
RISCO_MINIMO = 0.40
# risco_fogo = -999: o INPE não calculou o risco (não é risco zero).
# False: descarta o foco quando há filtro de risco. True: grava como "Sem informação".
INCLUIR_RISCO_NAO_CALCULADO = False
# Distância máxima (km) até a fazenda cadastrada mais próxima. None = Brasil inteiro.
# 50 km: guarda o que interessa às fazendas clientes e ao modelo de previsão, sem encher o banco
# (só setembro/2024 tem mais de 1 milhão de focos com risco alto no Brasil inteiro).
RAIO_MAXIMO_FAZENDA_KM = 50
# Focos do arquivo de 10 min que não foram confirmados por um arquivo diário depois deste prazo
# são removidos (o diário não os trouxe com risco >= mínimo).
DIAS_EXPIRAR_PENDENTE = 2
# -----------------------------------------------------------------------------------------------

CATEGORIA_PENDENTE = "Aguardando risco"
TAMANHO_LOTE = 5000
# Arquivos de 10 minutos baixados ao mesmo tempo (são pequenos; o tempo é quase todo espera da rede)
DOWNLOADS_SIMULTANEOS_10MIN = 8
VALOR_NAO_CALCULADO = -999
ORA_REGISTRO_DUPLICADO = 1  # ORA-00001: violação do índice único UX_CS_ALERTAS_CHAVE_FOCO

# A data dos arquivos vem em UTC; no banco gravamos no horário de Brasília (UTC-3)
FUSO_BRASILIA = datetime.timedelta(hours=-3)

# Casas decimais da chave do foco (3 casas ≈ 110 m): absorve diferenças de precisão entre arquivos
CASAS_DECIMAIS_CHAVE = 3

# Risco dos focos "Aguardando risco" (10 min): distância até a fazenda mais próxima
FAIXAS_DISTANCIA_KM = [
    (10, "Crítico"),
    (30, "Alto"),
    (50, "Médio"),
]
CATEGORIA_FORA_DO_RAIO = "Baixo"


# =============================================================================================
# Utilitários
# =============================================================================================

def ler_data_utc(texto):
    texto = str(texto or "").replace("T", " ").strip()[:19]
    # Caminho rápido para o formato do INPE (AAAA-MM-DD HH:MM[:SS]); strptime é ~10x mais lento
    if len(texto) >= 16 and texto[4] in "-/" and texto[7] in "-/" and texto[13] == ":":
        try:
            return datetime.datetime(int(texto[0:4]), int(texto[5:7]), int(texto[8:10]), int(texto[11:13]),
                                     int(texto[14:16]), int(texto[17:19]) if len(texto) >= 19 else 0)
        except ValueError:
            pass
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(texto, formato)
        except ValueError:
            continue
    return None


def gerar_chave_foco(satelite, data_utc, lat, lon):
    """Identificador do foco igual para qualquer arquivo do INPE. Ex.: 'GOES-19|202608282310|-12.346|-45.679'"""
    sat = str(satelite or "").strip().upper()
    return (f"{sat}|{data_utc.strftime('%Y%m%d%H%M')}"
            f"|{round(lat, CASAS_DECIMAIS_CHAVE):.{CASAS_DECIMAIS_CHAVE}f}"
            f"|{round(lon, CASAS_DECIMAIS_CHAVE):.{CASAS_DECIMAIS_CHAVE}f}")


def ler_risco(valor):
    """Risco como número, ou None quando o INPE não calculou (-999, vazio ou inválido)."""
    try:
        risco = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if risco <= VALOR_NAO_CALCULADO or risco < 0:
        return None
    return risco


def passa_filtro_risco(risco):
    if RISCO_MINIMO is None:
        return True
    if risco is None:
        return INCLUIR_RISCO_NAO_CALCULADO
    return risco > RISCO_MINIMO


def classificar_risco_fogo(risco):
    """Classes oficiais do Programa Queimadas para o risco de fogo (0 a 1)."""
    if risco is None:
        return "Sem informação"
    if risco <= 0.15:
        return "Mínimo"
    if risco <= 0.4:
        return "Baixo"
    if risco <= 0.7:
        return "Médio"
    if risco <= 0.95:
        return "Alto"
    return "Crítico"


def classificar_por_distancia(dist_km):
    if dist_km is not None:
        for limite, categoria in FAIXAS_DISTANCIA_KM:
            if dist_km <= limite:
                return categoria
    return CATEGORIA_FORA_DO_RAIO


def obter_recomendacao_queimada(categoria_risco):
    """Gera orientações operacionais específicas para o combate e prevenção de incêndios com base no risco."""
    risco = str(categoria_risco).lower()

    if "crítico" in risco or "alto" in risco:
        return (
            "🔥 ALERTA MÁXIMO DE INCÊNDIO: Risco elevado de propagação de fogo na região. "
            "Suspenda imediatamente operações com máquinas geradoras de faíscas próximas a áreas de vegetação, "
            "posicione frotas de combate e mantenha brigadas em estado de prontidão."
        )
    elif "médio" in risco:
        return (
            "⚠️ ATENÇÃO REDOBRADA (Risco de Fogo): Condições favoráveis a focos de calor. "
            "Monitore perímetro das lavouras e evite o uso de fogo para limpeza de terrenos."
        )
    else:
        return (
            "ℹ️ Vigilância padrão: Mantenha aceiros limpos e acompanhe os boletins de monitoramento de focos de calor."
        )


def orientacao_foco_pendente(dist_km):
    """Orientação para foco detectado cujo risco de fogo o INPE ainda não calculou ("Aguardando risco").
    Não usa o tom de "alerta máximo" (o risco oficial ainda não saiu). A escala muito próximo / nos arredores /
    distante e a faixa em km deixam claro qual foco está mais próximo da fazenda."""
    distancia = f"a {dist_km:.1f} km".replace(".", ",") if dist_km is not None else "a distância não informada"
    em_calculo = "Risco de fogo ainda em cálculo pelo INPE."
    if dist_km is not None and dist_km <= 10:
        return (f"⏳ FOCO MUITO PRÓXIMO: {distancia} (até 10 km). {em_calculo} Por precaução, mantenha "
                "a brigada de prontidão e evite operar máquinas que gerem faíscas perto da vegetação.")
    if dist_km is not None and dist_km <= 30:
        return (f"⏳ FOCO NOS ARREDORES: {distancia} (de 10 a 30 km). {em_calculo} "
                "Acompanhe a evolução e confira aceiros e equipamentos de combate.")
    return (f"⏳ FOCO DISTANTE: {distancia} (de 30 a 50 km). {em_calculo} "
            "Mantenha a vigilância e acompanhe os próximos boletins.")


def distancia_km(lat1, lon1, lat2, lon2):
    """Distância em linha reta (fórmula de Haversine)."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


class IndiceGeografico:
    """Agrupa pontos em células de 1 grau para achar o mais próximo sem comparar com todos."""

    def __init__(self, pontos):
        self.celulas = {}
        self.pontos = list(pontos)
        for ponto in self.pontos:
            chave = (math.floor(ponto["lat"]), math.floor(ponto["lon"]))
            self.celulas.setdefault(chave, []).append(ponto)

    def mais_proximo(self, lat, lon, raio_max_celulas=1):
        """Procura nas células vizinhas, ampliando a busca até raio_max_celulas se nada for encontrado."""
        base_lat, base_lon = math.floor(lat), math.floor(lon)
        for raio in range(1, raio_max_celulas + 1):
            melhor, melhor_dist = None, None
            for dlat in range(-raio, raio + 1):
                for dlon in range(-raio, raio + 1):
                    for ponto in self.celulas.get((base_lat + dlat, base_lon + dlon), []):
                        dist = distancia_km(lat, lon, ponto["lat"], ponto["lon"])
                        if melhor_dist is None or dist < melhor_dist:
                            melhor, melhor_dist = ponto, dist
            if melhor:
                return melhor, melhor_dist
        return None, None


class AreaAlcance:
    """Pré-filtro barato: células de 0,25 grau que ficam a até 'raio_km' de algum ponto (fazenda).
    Um foco fora dessas células certamente está além do raio e é descartado sem calcular distância."""
    PASSO = 4  # células por grau (0,25 grau)

    def __init__(self, pontos, raio_km):
        self.celulas = set()
        for ponto in pontos:
            dlat = raio_km / 111.0 + 0.01
            dlon = raio_km / (111.0 * max(math.cos(math.radians(ponto["lat"])), 0.1)) + 0.01
            for i in range(math.floor((ponto["lat"] - dlat) * self.PASSO), math.floor((ponto["lat"] + dlat) * self.PASSO) + 1):
                for j in range(math.floor((ponto["lon"] - dlon) * self.PASSO), math.floor((ponto["lon"] + dlon) * self.PASSO) + 1):
                    self.celulas.add((i, j))

    def celula(self, lat, lon):
        return (math.floor(lat * self.PASSO), math.floor(lon * self.PASSO))

    def contem(self, lat, lon):
        return self.celula(lat, lon) in self.celulas


# =============================================================================================
# Acesso ao INPE
# =============================================================================================

def requisitar(url, timeout=60):
    """GET com tentativas (retries) para contornar instabilidades temporárias de DNS ou rede."""
    for tentativa in range(1, 4):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout)
            if response.status_code == 200:
                return response
            if response.status_code == 404:
                return None
            logger.warning(f"   -> [AVISO] {url} retornou HTTP {response.status_code} (tentativa {tentativa}/3).")
        except requests.RequestException as e:
            logger.warning(f"   -> [AVISO] Tentativa {tentativa}/3 falhou: {e}")
        time.sleep(5)
    return None


def listar_diretorio(url):
    """Nomes dos arquivos .csv/.zip publicados no diretório do INPE (ou None se o diretório não respondeu)."""
    response = requisitar(url, timeout=30)
    if response is None:
        return None
    return sorted(set(re.findall(r'href="([^"/?]+\.(?:csv|zip))"', response.text, flags=re.IGNORECASE)))


def escolher_arquivo(nomes, trecho):
    """Arquivo cujo nome contém o trecho (ano, AAAAMM ou AAAAMMDD). Prefere .zip quando há os dois."""
    candidatos = sorted((n for n in nomes if trecho in n), key=lambda n: (not n.lower().endswith(".zip"), n))
    return candidatos[0] if candidatos else None


def baixar_para_arquivo_temporario(url, nome_arquivo):
    """Baixa em blocos para o disco (os anuais têm centenas de MB) mostrando o progresso."""
    for tentativa in range(1, 4):
        try:
            with requests.get(url, headers=HEADERS, timeout=120, stream=True) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    logger.warning(f"      [AVISO] {nome_arquivo} retornou HTTP {response.status_code} (tentativa {tentativa}/3).")
                    time.sleep(10)
                    continue
                total = int(response.headers.get("Content-Length") or 0)
                temporario = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(nome_arquivo)[1])
                baixado, proximo_aviso = 0, 25
                with temporario:
                    for bloco in response.iter_content(chunk_size=1024 * 1024):
                        temporario.write(bloco)
                        baixado += len(bloco)
                        if total > 20e6 and baixado * 100 / total >= proximo_aviso:
                            logger.info(f"      download: {int(baixado * 100 / total)}% ({baixado / 1e6:.0f} MB)")
                            while proximo_aviso <= baixado * 100 / total:
                                proximo_aviso += 25
                return temporario.name
        except requests.RequestException as e:
            logger.warning(f"      [AVISO] Download de {nome_arquivo} falhou (tentativa {tentativa}/3): {e}")
            time.sleep(10)
    return None


def abrir_csv(caminho):
    """Abre o CSV diretamente ou de dentro do .zip, lendo linha a linha (sem carregar tudo na memória)."""
    if caminho.lower().endswith(".zip"):
        pacote = zipfile.ZipFile(caminho)
        nome_csv = next(n for n in pacote.namelist() if n.lower().endswith(".csv"))
        return io.TextIOWrapper(pacote.open(nome_csv), encoding="utf-8", errors="ignore")
    return open(caminho, encoding="utf-8", errors="ignore")


# Nomes de coluna usados pelos diferentes arquivos do INPE (diário/mensal, anual,
# exportação do BDQueimadas e 10 minutos). A primeira coluna encontrada é usada.
ALIASES_COLUNAS = {
    "lat": ["lat", "latitude"],
    "lon": ["lon", "longitude"],
    "data": ["data_hora_gmt", "data_hora", "datahora", "data_pas", "data"],
    "satelite": ["satelite", "satelite_ref", "sat"],
    "risco": ["risco_fogo", "riscofogo"],
    "municipio_id": ["municipio_id", "id_municipio", "cod_municipio"],
    "municipio": ["municipio"],
    "estado": ["estado", "uf"],
    "bioma": ["bioma"],
}


def iterar_focos(arquivo_texto, estatisticas=None, filtro_posicao=None):
    """Lê qualquer CSV do INPE. Anual/mensal/diário têm cabeçalho (com ou sem risco de fogo);
    o de 10 minutos traz só lat, lon, satelite, data (às vezes sem cabeçalho).
    'estatisticas' (dict) recebe o cabeçalho, uma linha de exemplo e o total de linhas do arquivo.
    filtro_posicao(lat, lon): quando devolve False a linha é descartada logo depois de ler lat/lon
    (sem ler data nem montar o registro); ela só é contada em estatisticas["fora_area"], e a maior
    data do arquivo (com e sem risco) continua sendo acompanhada em "max_data" / "max_data_com_risco"."""
    estatisticas = estatisticas if estatisticas is not None else {}
    estatisticas.update({"linhas": 0, "cabecalho": [], "exemplo": [], "fora_area": 0,
                         "max_data": "", "max_data_com_risco": ""})
    leitor = csv.reader(arquivo_texto)
    primeira = next(leitor, None)
    if primeira is None:
        return
    cabecalho = [c.strip().lower().lstrip("\ufeff") for c in primeira]
    estatisticas["cabecalho"] = cabecalho

    def indice(campo):
        for nome in ALIASES_COLUNAS[campo]:
            if nome in cabecalho:
                return cabecalho.index(nome)
        return None

    if indice("lat") is not None and indice("lon") is not None:
        posicoes = {campo: indice(campo) for campo in ALIASES_COLUNAS}
    else:
        # Arquivo de 10 minutos sem cabeçalho: lat, lon, satelite, data
        posicoes = {campo: None for campo in ALIASES_COLUNAS}
        posicoes.update({"lat": 0, "lon": 1, "satelite": 2, "data": 3})
        leitor = iter([primeira] + list(leitor))
    tem_risco = posicoes["risco"] is not None
    estatisticas["tem_risco"] = tem_risco

    def valor(valores, campo):
        i = posicoes[campo]
        return valores[i].strip() if i is not None and i < len(valores) else ""

    for valores in leitor:
        if not valores:
            continue
        estatisticas["linhas"] += 1
        if not estatisticas["exemplo"]:
            estatisticas["exemplo"] = valores[:12]
        try:
            lat, lon = float(valor(valores, "lat")), float(valor(valores, "lon"))
        except ValueError:
            continue
        if filtro_posicao is not None and not filtro_posicao(lat, lon):
            estatisticas["fora_area"] += 1
            # Datas no mesmo formato dentro do arquivo: comparar o texto basta para achar a maior
            data_txt = valor(valores, "data")
            if data_txt > estatisticas["max_data"]:
                estatisticas["max_data"] = data_txt
            if tem_risco and data_txt > estatisticas["max_data_com_risco"] and ler_risco(valor(valores, "risco")) is not None:
                estatisticas["max_data_com_risco"] = data_txt
            continue
        data_utc = ler_data_utc(valor(valores, "data"))
        if not data_utc:
            continue
        yield {
            "lat": lat,
            "lon": lon,
            "satelite": valor(valores, "satelite") or "DESCONHECIDO",
            "data_utc": data_utc,
            "tem_risco": tem_risco,
            "risco": ler_risco(valor(valores, "risco")) if tem_risco else None,
            "municipio_id": valor(valores, "municipio_id"),
            "municipio": valor(valores, "municipio"),
            "estado": valor(valores, "estado"),
            "bioma": valor(valores, "bioma"),
        }


# =============================================================================================
# Gravação
# =============================================================================================

SQL_INSERT = """
    INSERT INTO CS_ALERTAS (TIPO_ALERTA, ORIGEM_ALERTA, DATA_HORA, CATEGORIA_RISCO, LATITUDE, LONGITUDE, ORIENTACAO, DETALHAMENTO_1, DETALHAMENTO_2, CHAVE_FOCO)
    VALUES (:1, 'INPE', TO_TIMESTAMP(:2, 'YYYY-MM-DD HH24:MI:SS'), :3, :4, :5, :6, :7, :8, :9)
"""
SQL_UPDATE = """
    UPDATE CS_ALERTAS
    SET TIPO_ALERTA = :1, DATA_HORA = TO_TIMESTAMP(:2, 'YYYY-MM-DD HH24:MI:SS'), CATEGORIA_RISCO = :3,
        LATITUDE = :4, LONGITUDE = :5, ORIENTACAO = :6, DETALHAMENTO_1 = :7, DETALHAMENTO_2 = :8
    WHERE CHAVE_FOCO = :9
"""
SQL_DELETE = "DELETE FROM CS_ALERTAS WHERE CHAVE_FOCO = :1"


class Carregador:
    """Mantém as referências (municípios e fazendas) e grava os focos com as regras de duplicidade."""

    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.cursor()

        self.cursor.execute("""
            SELECT MUNICIPIO_IBGE, MUNICIPIO, UF, LATITUDE, LONGITUDE FROM CS_MUNICIPIOS
            WHERE MUNICIPIO_IBGE IS NOT NULL AND LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL
        """)
        municipios = [
            {"ibge": str(ibge).strip(), "municipio": mun, "uf": uf, "lat": float(lat), "lon": float(lon)}
            for ibge, mun, uf, lat, lon in self.cursor.fetchall()
        ]
        self.mapa_ibge = {m["ibge"]: m for m in municipios}
        self.indice_municipios = IndiceGeografico(municipios)

        self.cursor.execute("""
            SELECT ID, NOME_FAZENDA, LATITUDE, LONGITUDE FROM CS_FAZENDAS
            WHERE LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL
        """)
        self.indice_fazendas = IndiceGeografico([
            {"id": id_faz, "nome": nome, "lat": float(lat), "lon": float(lon)}
            for id_faz, nome, lat, lon in self.cursor.fetchall()
        ])
        self.raio_proximidade = RAIO_MAXIMO_FAZENDA_KM or FAIXAS_DISTANCIA_KM[-1][0]
        # Pré-filtro: só focos nas células próximas de alguma fazenda seguem para o cálculo de distância
        self.area_fazendas = (AreaAlcance(self.indice_fazendas.pontos, RAIO_MAXIMO_FAZENDA_KM)
                              if RAIO_MAXIMO_FAZENDA_KM is not None else None)
        self.cache_municipios = {}

    def ultimo_registro_utc(self, incluir_pendentes=False):
        """Data/hora (UTC) do foco mais recente gravado. Os pendentes (10 min) não contam como carga concluída."""
        sql = "SELECT MAX(DATA_HORA) FROM CS_ALERTAS WHERE ORIGEM_ALERTA = :1 AND CHAVE_FOCO IS NOT NULL"
        if not incluir_pendentes:
            sql += " AND CATEGORIA_RISCO <> :2"
            self.cursor.execute(sql, [ORIGEM, CATEGORIA_PENDENTE])
        else:
            self.cursor.execute(sql, [ORIGEM])
        valor = self.cursor.fetchone()[0]
        return (valor - FUSO_BRASILIA) if valor else None

    def ultima_execucao_sucesso(self):
        """Data/hora da última execução deste serviço que terminou com SUCESSO no pipeline (CS_PIPELINE_LOGS).
        Serve de ponto de partida do diário: com os filtros de risco e raio, dias sem foco perto das
        fazendas são normais, então o último foco gravado não indica até onde os arquivos foram lidos."""
        try:
            self.cursor.execute("""
                SELECT MAX(DATA_EXECUCAO) FROM CS_PIPELINE_LOGS
                WHERE SCRIPT_NOME = 'alertas_queimadas.py' AND STATUS = 'SUCESSO'
            """)
            return self.cursor.fetchone()[0]
        except oracledb.DatabaseError:
            return None

    def carregar_existentes(self, padroes_chave):
        """Focos já gravados no período do arquivo: {chave: (categoria, det1, det2)}."""
        existentes = {}
        for padrao in padroes_chave:
            self.cursor.execute("""
                SELECT CHAVE_FOCO, CATEGORIA_RISCO, DETALHAMENTO_1, DETALHAMENTO_2 FROM CS_ALERTAS
                WHERE ORIGEM_ALERTA = :1 AND CHAVE_FOCO LIKE :2
            """, [ORIGEM, padrao])
            for chave, categoria, det1, det2 in self.cursor.fetchall():
                existentes[chave] = (categoria, det1, det2)
        return existentes

    def distancia_fazenda(self, lat, lon):
        return self.indice_fazendas.mais_proximo(lat, lon)

    def dentro_do_raio(self, dist_km):
        return RAIO_MAXIMO_FAZENDA_KM is None or (dist_km is not None and dist_km <= RAIO_MAXIMO_FAZENDA_KM)

    def municipio_mais_proximo(self, lat, lon):
        # Focos vizinhos (~1 km) caem no mesmo município: guarda o resultado para não recalcular
        chave = (round(lat, 2), round(lon, 2))
        if chave not in self.cache_municipios:
            if len(self.cache_municipios) > 200000:
                self.cache_municipios.clear()
            self.cache_municipios[chave] = self.indice_municipios.mais_proximo(lat, lon, raio_max_celulas=5)[0]
        return self.cache_municipios[chave]

    def montar_registro(self, foco, chave, fazenda_dist=None):
        """Registro para gravar e a distância até a fazenda mais próxima.
        Fora do raio devolve (None, dist_km) sem montar os textos (economiza a busca do município)."""
        lat, lon = foco["lat"], foco["lon"]

        fazenda, dist_km = fazenda_dist or self.distancia_fazenda(lat, lon)
        if not self.dentro_do_raio(dist_km):
            return None, dist_km

        # Município informado pelo INPE; se o código não for IBGE conhecido, usa o centroide mais próximo
        municipio = self.mapa_ibge.get(foco["municipio_id"])
        if not municipio:
            municipio = self.municipio_mais_proximo(lat, lon)
        if municipio:
            local = f"Município: {municipio['municipio']}/{municipio['uf']} (IBGE: {municipio['ibge']})"
        else:
            local = f"Município: {foco['municipio']}/{foco['estado']}"

        if fazenda and dist_km is not None and dist_km <= self.raio_proximidade:
            proximidade = f"Fazenda mais próxima: {fazenda['nome']} a {dist_km:.1f} km"
        else:
            proximidade = f"Sem fazendas cadastradas num raio de {self.raio_proximidade} km"

        if foco["tem_risco"]:
            categoria = classificar_risco_fogo(foco["risco"])
            orientacao = obter_recomendacao_queimada(categoria)
            risco_txt = f"{foco['risco']:.2f}" if foco["risco"] is not None else "não calculado (-999)"
        else:
            categoria = CATEGORIA_PENDENTE
            orientacao = orientacao_foco_pendente(dist_km)
            risco_txt = "aguardando arquivo diário do INPE"

        det1 = f"{local} - Lat: {lat}, Lon: {lon}"
        det2 = f"Satélite: {foco['satelite']} | Risco de Fogo: {risco_txt} | Bioma: {foco['bioma']} | {proximidade}"
        data_brasilia = (foco["data_utc"] + FUSO_BRASILIA).strftime("%Y-%m-%d %H:%M:%S")

        return [f"Foco de Incêndio / Queimada ({foco['satelite']})", data_brasilia, categoria,
                lat, lon, orientacao, det1, det2, chave], dist_km

    def processar_arquivo(self, url, nome_arquivo, padroes_chave, prefixo, apos_utc=None, exige_risco=False,
                          amostra_detalhes=None, caminho=None, existentes=None, silencioso=False):
        """Baixa e grava um arquivo. Devolve dict com contadores, ou None se o download falhou.
        apos_utc: ignora focos até esse momento (período já coberto por um arquivo com risco).
        exige_risco: arquivos do histórico (mensal/diário) precisam da coluna de risco de fogo.
        amostra_detalhes: para focos JÁ gravados, confere os textos (categoria, município, fazenda...) só
        de 1 a cada N (conferência amostral, bem mais rápida). None confere todos. Focos novos são sempre
        inseridos e focos que saíram das regras (risco ou raio) são sempre removidos.
        caminho: arquivo já baixado (não baixa de novo). existentes: dict de focos já gravados, carregado
        uma vez e compartilhado entre vários arquivos (evita consultar a tabela a cada arquivo).
        silencioso: não escreve as linhas "baixando"/"concluído" (quem chama mostra o progresso)."""
        if caminho is None:
            logger.info(f"{prefixo} {nome_arquivo}: baixando...")
            caminho = baixar_para_arquivo_temporario(url, nome_arquivo)
            if not caminho:
                return None

        c = {"lidos": 0, "novos": 0, "atualizados": 0, "removidos": 0,
             "sem_mudanca": 0, "abaixo_risco": 0, "fora_raio": 0, "ja_cobertos": 0, "ultimo_lido": None,
             "ultimo_com_risco": None}
        if existentes is None:
            existentes = self.carregar_existentes(padroes_chave)
        inserir, atualizar, remover = [], [], []
        contador_existentes = 0

        # Pré-filtro de posição: descarta sem mais cálculos o foco longe de todas as fazendas, a não ser
        # que haja foco gravado naquela célula (ele pode precisar ser removido/atualizado).
        filtro_posicao = None
        if self.area_fazendas is not None:
            area = self.area_fazendas
            celulas_gravadas = set()
            for chave_existente in existentes:
                try:
                    _, _, lat_txt, lon_txt = chave_existente.split("|")
                    celulas_gravadas.add(area.celula(float(lat_txt), float(lon_txt)))
                except ValueError:
                    continue

            def filtro_posicao(lat, lon):
                celula = area.celula(lat, lon)
                return celula in area.celulas or celula in celulas_gravadas

        def gravar():
            if inserir:
                # batcherrors: foco que já existe (ORA-00001) é ignorado sem interromper o lote
                self.cursor.executemany(SQL_INSERT, inserir, batcherrors=True)
                erros = self.cursor.getbatcherrors()
                outros = [e for e in erros if e.code != ORA_REGISTRO_DUPLICADO]
                if outros:
                    raise Exception(f"{len(outros)} erro(s) ao gravar {nome_arquivo}: {outros[0].message}")
                c["novos"] += len(inserir) - len(erros)
                c["sem_mudanca"] += len(erros)
            if atualizar:
                self.cursor.executemany(SQL_UPDATE, atualizar)
                c["atualizados"] += len(atualizar)
            if remover:
                self.cursor.executemany(SQL_DELETE, remover)
                c["removidos"] += len(remover)
            self.connection.commit()
            inserir.clear(); atualizar.clear(); remover.clear()

        try:
            estatisticas = {}
            with abrir_csv(caminho) as arquivo_texto:
                for foco in iterar_focos(arquivo_texto, estatisticas, filtro_posicao):
                    if exige_risco and not foco["tem_risco"]:
                        raise Exception(
                            f"{nome_arquivo} não tem a coluna de risco de fogo, então a regra de risco mínimo "
                            f"({RISCO_MINIMO}) não pode ser aplicada. Cabeçalho: {estatisticas.get('cabecalho')}")
                    c["lidos"] += 1
                    if c["ultimo_lido"] is None or foco["data_utc"] > c["ultimo_lido"]:
                        c["ultimo_lido"] = foco["data_utc"]
                    # O INPE calcula o risco do dia algumas horas depois: focos ainda com -999 no arquivo
                    # diário não contam como "cobertos" (o 10 min grava como "Aguardando risco").
                    if foco["risco"] is not None and (c["ultimo_com_risco"] is None or foco["data_utc"] > c["ultimo_com_risco"]):
                        c["ultimo_com_risco"] = foco["data_utc"]
                    if apos_utc and foco["data_utc"] <= apos_utc:
                        c["ja_cobertos"] += 1
                        continue
                    chave = gerar_chave_foco(foco["satelite"], foco["data_utc"], foco["lat"], foco["lon"])
                    anterior = existentes.get(chave)

                    if foco["tem_risco"] and not passa_filtro_risco(foco["risco"]):
                        c["abaixo_risco"] += 1
                        # Risco ainda não calculado (-999 no diário do dia) e foco pendente do 10 min:
                        # mantém "Aguardando risco" até o INPE calcular (ou até expirar)
                        if foco["risco"] is None and anterior and anterior[0] == CATEGORIA_PENDENTE:
                            continue
                        # Já estava gravado (ex.: veio do 10 min) e o risco oficial ficou abaixo do mínimo
                        if anterior:
                            remover.append([chave])
                            del existentes[chave]
                        continue

                    # 10 min sem risco: nunca sobrescreve um foco que já existe (pendente ou confirmado)
                    if not foco["tem_risco"] and anterior:
                        c["sem_mudanca"] += 1
                        continue

                    fazenda_dist = self.distancia_fazenda(foco["lat"], foco["lon"])
                    if not self.dentro_do_raio(fazenda_dist[1]):
                        c["fora_raio"] += 1
                        if anterior:
                            remover.append([chave])
                            del existentes[chave]
                        continue

                    # Conferência amostral: foco já gravado e ainda dentro das regras -> só 1 a cada N
                    # tem os textos comparados; os demais contam como "sem mudança".
                    if anterior is not None and amostra_detalhes:
                        contador_existentes += 1
                        if contador_existentes % amostra_detalhes:
                            c["sem_mudanca"] += 1
                            continue

                    registro, _ = self.montar_registro(foco, chave, fazenda_dist)

                    atual = (registro[2], registro[6], registro[7])  # categoria, det1, det2
                    if anterior is None:
                        inserir.append(registro)
                    elif anterior != atual:
                        atualizar.append(registro)
                    else:
                        c["sem_mudanca"] += 1
                    existentes[chave] = atual

                    if len(inserir) + len(atualizar) + len(remover) >= TAMANHO_LOTE:
                        gravar()
                        logger.info(f"{prefixo} {nome_arquivo}: {c['lidos']:,} lidos | {c['novos']:,} novos | "
                                    f"{c['atualizados']:,} atualizados...")
            gravar()
            if exige_risco and estatisticas.get("linhas", 0) > 0 and not estatisticas.get("tem_risco"):
                raise Exception(
                    f"{nome_arquivo} não tem a coluna de risco de fogo, então a regra de risco mínimo "
                    f"({RISCO_MINIMO}) não pode ser aplicada. Cabeçalho: {estatisticas.get('cabecalho')}")
            # Linhas descartadas no pré-filtro de posição também foram lidas (e estão fora do raio)
            c["lidos"] += estatisticas["fora_area"]
            c["fora_raio"] += estatisticas["fora_area"]
            for campo_c, campo_e in (("ultimo_lido", "max_data"), ("ultimo_com_risco", "max_data_com_risco")):
                data_extra = ler_data_utc(estatisticas[campo_e]) if estatisticas[campo_e] else None
                if data_extra and (c[campo_c] is None or data_extra > c[campo_c]):
                    c[campo_c] = data_extra
            # Arquivo com dados mas nenhuma linha reconhecida: formato diferente do esperado.
            # Para aqui (sem seguir para as próximas fases) e mostra o cabeçalho para ajuste.
            if estatisticas.get("linhas", 0) > 0 and c["lidos"] == 0:
                raise Exception(
                    f"Formato não reconhecido em {nome_arquivo}: {estatisticas['linhas']:,} linhas e nenhuma lida. "
                    f"Cabeçalho: {estatisticas.get('cabecalho')} | Exemplo: {estatisticas.get('exemplo')}")
        finally:
            os.remove(caminho)

        if silencioso:
            return c
        logger.info(f"{prefixo} {nome_arquivo}: concluído. {c['lidos']:,} lidos | {c['novos']:,} novos | "
                    f"{c['atualizados']:,} atualizados | {c['removidos']:,} removidos | {c['sem_mudanca']:,} sem mudança | "
                    f"{c['abaixo_risco']:,} abaixo do risco mínimo ou -999 | {c['fora_raio']:,} fora do raio"
                    + (f" | {c['ja_cobertos']:,} já cobertos pelo diário" if c["ja_cobertos"] else ""))
        return c

    def expirar_pendentes(self, ultimo_confirmado_utc):
        """Remove focos do 10 min que o arquivo diário não confirmou dentro do prazo."""
        if not ultimo_confirmado_utc:
            return 0
        limite = (ultimo_confirmado_utc + FUSO_BRASILIA - datetime.timedelta(days=DIAS_EXPIRAR_PENDENTE))
        self.cursor.execute("""
            DELETE FROM CS_ALERTAS
            WHERE ORIGEM_ALERTA = :1 AND CATEGORIA_RISCO = :2 AND DATA_HORA < TO_TIMESTAMP(:3, 'YYYY-MM-DD HH24:MI:SS')
        """, [ORIGEM, CATEGORIA_PENDENTE, limite.strftime("%Y-%m-%d %H:%M:%S")])
        removidos = self.cursor.rowcount
        self.connection.commit()
        return removidos


# =============================================================================================
# Execução por fases
# =============================================================================================

def fmt(dt):
    return (dt + FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M") if dt else "nenhum"


def executar():
    logger.info("[INPE - QUEIMADAS] Iniciando sincronização (incremental: diário -> 10 min)...")
    agora_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    connection = None
    total = {"novos": 0, "atualizados": 0, "removidos": 0}
    resumo = []

    def somar(c):
        for k in total:
            total[k] += c[k]

    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        carregador = Carregador(connection)

        ultimo = carregador.ultimo_registro_utc()
        logger.info(f"   -> Último registro confirmado no banco: {fmt(ultimo)} (horário de Brasília)")

        # ---------------- FASE 1: DIÁRIO ----------------
        # Começa no MAIS ANTIGO entre: o dia do último foco confirmado e a última execução com sucesso
        # (menos DIAS_RELEITURA_DIARIO). Assim um log de sucesso antigo (ex.: de antes de a tabela ser
        # limpa) não faz pular dias que não estão no banco. Nunca volta mais que DIAS_MAX_DIARIO:
        # períodos antigos são do tratamento/queimadas_historico.py.
        ultima_execucao = carregador.ultima_execucao_sucesso()
        candidatos = []
        if ultimo:
            candidatos.append(ultimo.date())
        if ultima_execucao:
            candidatos.append(ultima_execucao.date() - datetime.timedelta(days=DIAS_RELEITURA_DIARIO))
        inicio = min(candidatos) if candidatos else agora_utc.date()
        limite = agora_utc.date() - datetime.timedelta(days=DIAS_MAX_DIARIO)
        if inicio < limite:
            logger.warning(f"   -> [AVISO] O período antes de {limite:%d/%m/%Y} não é coberto pelo diário. "
                           f"Se faltar histórico, rode: python tratamento/queimadas_historico.py --inicio "
                           f"{inicio:%Y-%m}")
            inicio = limite
        dias = [inicio + datetime.timedelta(days=d) for d in range((agora_utc.date() - inicio).days + 1)]
        ultimo_lido_diario = None
        if dias:
            logger.info(f"[Fase 1/2 - Diário] {len(dias)} dia(s): {dias[0]:%d/%m} a {dias[-1]:%d/%m}")
            nomes = listar_diretorio(URL_DIARIO) or []
            nao_publicados = []
            for i, dia in enumerate(dias, start=1):
                periodo = dia.strftime("%Y%m%d")
                nome = escolher_arquivo(nomes, periodo)
                if not nome:
                    nao_publicados.append(f"{dia:%d/%m}")
                    continue
                c = carregador.processar_arquivo(URL_DIARIO + nome, nome, [f"%|{periodo}____|%"],
                                                 f"   [Diário {i}/{len(dias)}]", exige_risco=True)
                if c:
                    somar(c)
                    resumo.append(f"{periodo}: {c['novos']:,} novos")
                    if c["ultimo_com_risco"] and (ultimo_lido_diario is None or c["ultimo_com_risco"] > ultimo_lido_diario):
                        ultimo_lido_diario = c["ultimo_com_risco"]
            if nao_publicados:
                logger.warning(f"   -> [AVISO] Sem arquivo diário no INPE para: {', '.join(nao_publicados)}")
        ultimo = carregador.ultimo_registro_utc()

        # ---------------- FASE 2: 10 MINUTOS ----------------
        # Focos até "coberto_ate" já vieram no diário com risco calculado (gravados ou descartados pelo
        # risco); o 10 min só completa depois disso
        coberto_ate = max((d for d in [ultimo, ultimo_lido_diario] if d), default=None)
        ultimo_geral = carregador.ultimo_registro_utc(incluir_pendentes=True)
        referencia = max((d for d in [ultimo_geral, coberto_ate] if d), default=None)
        corte = (referencia - datetime.timedelta(minutes=10)) if referencia else None
        nomes = listar_diretorio(URL_10MIN) or []
        arquivos_10min = []
        for nome in nomes:
            m = re.search(r"(\d{8})_?(\d{4})", nome)
            momento = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M") if m else None
            if corte is None or momento is None or momento >= corte:
                arquivos_10min.append((nome, momento))
        logger.info(f"[Fase 2/2 - 10 min] {len(arquivos_10min)} arquivo(s) após {fmt(corte)}")
        if arquivos_10min:
            # Focos já gravados nos dias desses arquivos: uma consulta só para todos os arquivos
            dias_chave = set()
            for _, momento in arquivos_10min:
                base = momento or agora_utc
                dias_chave.update({base.date(), (base - datetime.timedelta(days=1)).date()})
            existentes = carregador.carregar_existentes([f"%|{d:%Y%m%d}____|%" for d in sorted(dias_chave)])

            # Downloads em paralelo; a gravação continua em ordem, um arquivo por vez
            parcial = {"lidos": 0, "novos": 0, "atualizados": 0, "removidos": 0, "fora_raio": 0, "ja_cobertos": 0}
            falhas = []
            inicio_fase = time.time()
            with ThreadPoolExecutor(max_workers=DOWNLOADS_SIMULTANEOS_10MIN) as executor:
                baixados = executor.map(lambda item: baixar_para_arquivo_temporario(URL_10MIN + item[0], item[0]),
                                        arquivos_10min)
                for i, ((nome, momento), caminho) in enumerate(zip(arquivos_10min, baixados), start=1):
                    if not caminho:
                        falhas.append(nome)
                    else:
                        c = carregador.processar_arquivo(URL_10MIN + nome, nome, [], "   [10 min]",
                                                         apos_utc=coberto_ate, caminho=caminho,
                                                         existentes=existentes, silencioso=True)
                        somar(c)
                        for k in parcial:
                            parcial[k] += c[k]
                    if i % 20 == 0 or i == len(arquivos_10min):
                        ate = fmt(momento) if momento else nome
                        logger.info(f"   [10 min] {i}/{len(arquivos_10min)} arquivos (até {ate}) | "
                                    f"{parcial['lidos']:,} lidos | {parcial['novos']:,} novos | "
                                    f"{parcial['atualizados']:,} atualizados | {parcial['removidos']:,} removidos | "
                                    f"{parcial['ja_cobertos']:,} já cobertos pelo diário | {parcial['fora_raio']:,} fora do raio"
                                    f" | {time.time() - inicio_fase:.0f}s")
            if falhas:
                logger.warning(f"   -> [AVISO] {len(falhas)} arquivo(s) de 10 min não baixaram depois de 3 "
                               f"tentativas (os focos deles chegam depois pelo arquivo diário): {', '.join(falhas[:5])}"
                               + (" ..." if len(falhas) > 5 else ""))

        expirados = carregador.expirar_pendentes(ultimo)
        if expirados:
            logger.info(f"   -> {expirados} foco(s) do 10 min removidos: o arquivo diário não confirmou risco >= mínimo.")
        total["removidos"] += expirados

        ultimo_geral = carregador.ultimo_registro_utc(incluir_pendentes=True)
        msg = (f"Sincronização do INPE concluída até {fmt(ultimo_geral)}. {total['novos']:,} novos, "
               f"{total['atualizados']:,} atualizados, {total['removidos']:,} removidos.")
        if resumo:
            msg += " " + "; ".join(resumo[-12:])
        logger.info(f"   -> {msg}")
        return "SUCESSO", msg[:4000], total["novos"]

    except Exception as e:
        if connection:
            connection.rollback()
        msg_erro = (f"Falha na sincronização do INPE: {str(e)} ({total['novos']:,} foco(s) gravados antes da falha; "
                    f"a próxima execução continua a partir do último registro)")
        logger.error(f"[ERRO GERAL] {msg_erro}")
        return "ERRO", msg_erro, total["novos"]
    finally:
        if connection:
            connection.close()


def registrar_log_execucao_manual(status, mensagem, qtd):
    """Grava em CS_PIPELINE_LOGS a execução feita à mão (python servicos/alertas_queimadas.py).
    Só é chamada no bloco __main__: quando o pipeline roda o serviço, quem grava o log é o pipeline,
    então cada execução gera exatamente um registro. O ID 'MANUAL_...' diferencia as execuções à mão.
    O log com SUCESSO também serve de ponto de partida para a próxima leitura do diário."""
    exec_id = "MANUAL_" + os.urandom(4).hex().upper()
    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()
        cursor.setinputsizes(None, None, None, None, oracledb.DB_TYPE_CLOB, None)
        cursor.execute("""
            INSERT INTO CS_PIPELINE_LOGS
            (PIPELINE_EXEC_ID, SCRIPT_NOME, TENTATIVA, STATUS, MENSAGEM_RETORNO, REGISTROS_PROCESSADOS)
            VALUES (:1, :2, :3, :4, :5, :6)
        """, [exec_id, "alertas_queimadas.py", 1, str(status)[:20], str(mensagem or ""), int(qtd or 0)])
        connection.commit()
        cursor.close()
        connection.close()
        logger.info(f"   -> Log registrado em CS_PIPELINE_LOGS [{exec_id} | Status: {status} | Registros: {qtd}]")
    except Exception as e:
        logger.error(f"   -> [ERRO DE LOG ORACLE] Não foi possível gravar o log da execução manual: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info("--- EXECUTANDO SERVIÇO QUEIMADAS (Isolada) ---")
    try:
        status, mensagem, qtd = executar()
    except KeyboardInterrupt:
        status, mensagem, qtd = "INTERROMPIDO", "Execução manual interrompida (Ctrl+C).", 0
        logger.warning("   -> Execução interrompida manualmente.")
    registrar_log_execucao_manual(status, mensagem, qtd)
