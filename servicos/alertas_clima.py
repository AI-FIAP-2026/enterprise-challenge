# -*- coding: utf-8 -*-
import sys
import os

# --- AJUSTE DE DIRETÓRIO PARA O PIPELINE E EXECUÇÃO ISOLADA ---
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)
# -------------------------------------------------------------

requests = __import__('requests')
datetime = __import__('datetime')
oracledb = __import__('oracledb')
from auth import USER, PASSWORD, DSN

def obter_recomendacao_agricola(tipo_alerta, categoria_risco):
    """Gera orientações operacionais específicas para maquinário agrícola e lavoura com base no risco."""
    tipo = tipo_alerta.lower()
    risco = categoria_risco.lower()
    
    if "hidrológico" in tipo or "chuva" in tipo or "inundação" in tipo:
        if risco in ["alto", "crítico"]:
            return (
                "⚠️ PARALISAÇÃO RECOMENDADA: Risco iminente de alagamentos e enxurradas. "
                "Retire imediatamente tratores, colhedoras e frotas de áreas baixas e margens de rios."
            )
        else:
            return (
                "ℹ️ ATENÇÃO REDOBRADA (Hidrológico): Evite tráfego de máquinas pesadas em solos encharcados "
                "para prevenir compactação severa."
            )
    elif "geológico" in tipo or "movimento" in tipo or "deslizamento" in tipo:
        return (
            "🛑 ALERTA MÁXIMO DE ENCOSTA: Alto risco de deslizamentos nas imediações. "
            "Proibida a circulação de frotas e maquinários próximos a taludes e estradas vicinais instáveis."
        )
    else:
        return "Acompanhe os boletins meteorológicos locais e redobre a vigilância nas operações de campo."

def sincronizar_escalavel_cemaden():
    print("\n[CEMADEN - TEMPO REAL] Sincronizando alertas vigentes (Sem duplicidade)...")
    
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    cursor = connection.cursor()
    total_registros_processados = 0
    
    try:
        # 1. Busca os códigos IBGE das fazendas cadastradas no Oracle
        cursor.execute("SELECT DISTINCT CODIGO_IBGE, MUNICIPIO, ESTADO FROM CS_FAZENDAS WHERE CODIGO_IBGE IS NOT NULL")
        fazendas_cadastradas = cursor.fetchall()
        
        # Usamos um dicionário base
        mapa_fazendas = {}
        if fazendas_cadastradas:
            for row in fazendas_cadastradas:
                ibge = str(row[0]).strip()
                mapa_fazendas[ibge] = {"municipio": row[1], "uf": row[2]}

        # 2. Lista complementar robusta com polos agrícolas (com chaves limpas e únicas)
        municipios_extras = [
            ("2918407", "Luís Eduardo Magalhães", "BA"),
            ("2925758", "Riachão das Neves", "BA"),
            ("2929206", "São Desidério", "BA"),
            ("2900705", "Alagoinhas", "BA"),
            ("2910800", "Feira de Santana", "BA"),
            ("2927408", "Salvador", "BA"),
            ("2933307", "Vitória da Conquista", "BA"),
            ("2905704", "Catu", "BA"),
            ("2928703", "Santo Antônio de Jesus", "BA"),
            ("2930709", "Serrinha", "BA"),
            ("2101202", "Balsas", "MA"),
            ("2105302", "Imperatriz", "MA"),
            ("2111300", "São Luís", "MA"),
            ("2103000", "Caxias", "MA"),
            ("2112209", "Timon", "MA"),
            ("1721000", "Palmas", "TO"),
            ("1702109", "Araguaína", "TO"),
            ("1707009", "Gurupi", "TO"),
            ("1716109", "Paraíso do Tocantins", "TO"),
            ("2211001", "Teresina", "PI"),
            ("2208007", "Parnaíba", "PI"),
            ("2203909", "Floriano", "PI"),
            ("2207702", "Picos", "PI"),
            ("2201907", "Bom Jesus", "PI"),
            ("2210607", "Uruçuí", "PI"),
            ("5103304", "Sorriso", "MT"),
            ("5107602", "Sinop", "MT"),
            ("5107909", "Rondonópolis", "MT"),
            ("5103403", "Cuiabá", "MT"),
            ("5108402", "Várzea Grande", "MT"),
            ("5106208", "Nova Mutum", "MT"),
            ("5105606", "Lucas do Rio Verde", "MT"),
            ("5105259", "Campo Novo do Parecis", "MT"),
            ("5103854", "Diamantino", "MT"),
            ("5102633", "Canarana", "MT"),
            ("5103056", "Chapada dos Guimarães", "MT"),
            ("5106422", "Primavera do Leste", "MT"),
            ("5107875", "Querência", "MT"),
            ("5108857", "Sapezal", "MT"),
            ("5101809", "Barra do Garças", "MT"),
            ("5103202", "Juscimeira", "MT"),
            ("5104100", "Jaciara", "MT"),
            ("5003702", "Dourados", "MS"),
            ("5002704", "Campo Grande", "MS"),
            ("5007901", "Três Lagoas", "MS"),
            ("5008305", "Terenos", "MS"),
            ("5005400", "Maracaju", "MS"),
            ("5006903", "Ponta Porã", "MS"),
            ("5004106", "Itaporã", "MS"),
            ("5005681", "Mundo Novo", "MS"),
            ("5006275", "Naviraí", "MS"),
            ("5007208", "Ribas do Rio Pardo", "MS"),
            ("5007406", "São Gabriel do Oeste", "MS"),
            ("5002902", "Caarapó", "MS"),
            ("5208707", "Goiânia", "GO"),
            ("5201405", "Anápolis", "GO"),
            ("5218805", "Rio Verde", "GO"),
            ("5211503", "Itumbiara", "GO"),
            ("5215232", "Jataí", "GO"),
            ("5213103", "Mineiros", "GO"),
            ("5212501", "Luziânia", "GO"),
            ("5200134", "Abadiânia", "GO"),
            ("5200258", "Águas Lindas de Goiás", "GO"),
            ("5205109", "Catalão", "GO"),
            ("5208004", "Formosa", "GO"),
            ("5221403", "Santo Antônio de Goiás", "GO"),
            ("5222005", "Senador Canedo", "GO"),
            ("5220454", "Santa Helena de Goiás", "GO"),
            ("5204508", "Cachoeira Alta", "GO"),
            ("5203609", "Caiapônia", "GO"),
            ("4106902", "Curitiba", "PR"),
            ("4113700", "Londrina", "PR"),
            ("4115200", "Maringá", "PR"),
            ("4108304", "Foz do Iguaçu", "PR"),
            ("4125506", "São José dos Pinhais", "PR"),
            ("4119905", "Ponta Grossa", "PR"),
            ("4104303", "Campo Mourão", "PR"),
            ("4122206", "Paranavaí", "PR"),
            ("4124509", "Rolândia", "PR"),
            ("4101804", "Araucária", "PR"),
            ("4107207", "Dois Vizinhos", "PR"),
            ("4107652", "Fazenda Rio Grande", "PR"),
            ("4109302", "Guarapuava", "PR"),
            ("4118204", "Paranaguá", "PR"),
            ("4127502", "Toledo", "PR"),
            ("4128658", "Umuarama", "PR"),
            ("4128708", "União da Vitória", "PR"),
            ("4104105", "Cambé", "PR"),
            ("4205407", "Florianópolis", "SC"),
            ("4209102", "Joinville", "SC"),
            ("4208203", "Itajaí", "SC"),
            ("4202404", "Blumenau", "SC"),
            ("4211900", "Palhoça", "SC"),
            ("4202909", "Brusque", "SC"),
            ("4204202", "Chapecó", "SC"),
            ("4204608", "Criciúma", "SC"),
            ("4216602", "São José", "SC"),
            ("4218705", "Tubarão", "SC"),
            ("4208906", "Lages", "SC"),
            ("4314902", "Porto Alegre", "RS"),
            ("4305108", "Caxias do Sul", "RS"),
            ("4318705", "São Leopoldo", "RS"),
            ("4313409", "Novo Hamburgo", "RS"),
            ("4314100", "Passo Fundo", "RS"),
            ("4322400", "Uruguaiana", "RS"),
            ("4321608", "Santa Maria", "RS"),
            ("4316907", "Santa Cruz do Sul", "RS"),
            ("4304606", "Canoas", "RS"),
            ("4309308", "Gramado", "RS"),
            ("4315602", "Osório", "RS"),
            ("4317103", "Santana do Livramento", "RS"),
            ("4323002", "Viamão", "RS"),
            ("4303103", "Cachoeira do Sul", "RS"),
            ("4307708", "Esteio", "RS"),
            ("3550308", "São Paulo", "SP"),
            ("3509502", "Campinas", "SP"),
            ("3549805", "São José dos Campos", "SP"),
            ("3557105", "Votuporanga", "SP"),
            ("3554003", "Tatuí", "SP"),
            ("3550704", "Sorocaba", "SP"),
            ("3543402", "Ribeirão Preto", "SP"),
            ("3538709", "Piracicaba", "SP"),
            ("3525300", "Jaú", "SP"),
            ("3526902", "Limeira", "SP"),
            ("3522208", "Guarulhos", "SP"),
            ("3548708", "São Bernardo do Campo", "SP"),
            ("3547809", "Santo André", "SP"),
            ("3548500", "Santos", "SP"),
            ("3534401", "Osasco", "SP"),
            ("3556206", "Valinhos", "SP"),
            ("3556701", "Vinhedo", "SP"),
            ("3505708", "Barretos", "SP"),
            ("3502804", "Araraquara", "SP"),
            ("3552205", "São Carlos", "SP"),
            ("3510609", "Catanduva", "SP"),
            ("3513009", "Cotia", "SP"),
            ("3515004", "Embu das Artes", "SP"),
            ("3516309", "Franca", "SP"),
            ("3518701", "Guaratinguetá", "SP"),
            ("3523909", "Itu", "SP"),
            ("3524709", "Jundiaí", "SP"),
            ("3528502", "Mogi das Cruzes", "SP"),
            ("3533403", "Ourinhos", "SP"),
            ("3536505", "Paulínia", "SP"),
            ("3541000", "Presidente Prudente", "SP"),
            ("3543907", "Rio Claro", "SP"),
            ("3545209", "Salto", "SP"),
            ("3549904", "São José do Rio Preto", "SP"),
            ("3553908", "Tarumã", "SP"),
            ("3106200", "Belo Horizonte", "MG"),
            ("3118601", "Contagem", "MG"),
            ("3162708", "Sete Lagoas", "MG"),
            ("3170206", "Uberlândia", "MG"),
            ("3143907", "Muriaé", "MG"),
            ("3136703", "Juiz de Fora", "MG"),
            ("3152501", "Pouso Alegre", "MG"),
            ("3118304", "Conselheiro Lafaiete", "MG"),
            ("3171303", "Varginha", "MG"),
            ("3148005", "Patos de Minas", "MG"),
            ("3169307", "Três Pontas", "MG"),
            ("3131308", "Ipatinga", "MG"),
            ("3106705", "Betim", "MG"),
            ("3127702", "Governador Valadares", "MG"),
            ("3135101", "Janaúba", "MG"),
            ("3143105", "Montes Claros", "MG"),
            ("3157807", "Santa Luzia", "MG"),
            ("3304557", "Rio de Janeiro", "RJ"),
            ("3303500", "Nova Iguaçu", "RJ"),
            ("3301702", "Duque de Caxias", "RJ"),
            ("3304904", "São Gonçalo", "RJ"),
            ("3303302", "Niterói", "RJ"),
            ("3306305", "Volta Redonda", "RJ"),
            ("3303906", "Petrópolis", "RJ"),
            ("3305802", "Teresópolis", "RJ"),
            ("3205309", "Vitória", "ES"),
            ("3205200", "Vila Velha", "ES"),
            ("3201308", "Cariacica", "ES"),
            ("3204907", "Serra", "ES"),
            ("3202405", "Guarapari", "ES"),
            ("3201209", "Cachoeiro de Itapemirim", "ES"),
            ("1501402", "Belém", "PA"),
            ("1502400", "Castanhal", "PA"),
            ("1504208", "Marabá", "PA"),
            ("1505502", "Parauapebas", "PA"),
            ("1507300", "Santarém", "PA"),
            ("1500800", "Ananindeua", "PA"),
            ("1100205", "Porto Velho", "RO"),
            ("1100122", "Ji-Paraná", "RO"),
            ("1100049", "Ariquemes", "RO"),
            ("1100155", "Ouro Preto do Oeste", "RO"),
            ("1302603", "Manaus", "AM"),
            ("1301407", "Itacoatiara", "AM"),
            ("1200401", "Rio Branco", "AC"),
            ("1600303", "Macapá", "AP"),
            ("1400100", "Boa Vista", "RR")
        ]

        # Insere apenas se o IBGE ainda não existir no dicionário, evitando sobrescrever ou perder chaves
        for ibge, mun, uf in municipios_extras:
            if ibge not in mapa_fazendas:
                mapa_fazendas[ibge] = {"municipio": mun, "uf": uf}

        lista_ibges = list(mapa_fazendas.keys())
        
        print(f"   -> Total consolidado e sem duplicidade: {len(lista_ibges)} municípios para monitoramento.")

        for ibge in lista_ibges:
            info = mapa_fazendas[ibge]
            municipio_nome = info["municipio"]
            uf_nome = info["uf"]

            url_api = f"https://sws.cemaden.gov.br/PED/api/alertas/municipio/{ibge}"
            headers = {"User-Agent": "Mozilla/5.0"}

            try:
                response = requests.get(url_api, headers=headers, timeout=8)
                if response.status_code != 200:
                    url_alt = f"https://painelalertas.cemaden.gov.br/api/alertas?ibge={ibge}"
                    response = requests.get(url_alt, headers=headers, timeout=8)

                dados_eventos = response.json() if response.status_code == 200 else []
            except Exception:
                continue

            if not dados_eventos:
                continue

            for evento in dados_eventos:
                tipo_alerta = evento.get("tipoRisco", evento.get("tipo_alerta", "Risco de Desastre Natural"))
                nivel = evento.get("nivelRisco", evento.get("nivel", evento.get("severidade", "Moderado")))
                data_str = evento.get("dataHora", evento.get("data", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                
                if "T" in str(data_str):
                    data_str = str(data_str).replace("T", " ")[:19]

                sql_check = """
                    SELECT COUNT(1) FROM CS_ALERTAS 
                    WHERE ORIGEM_ALERTA = 'CEMADEN' AND DETALHAMENTO_1 LIKE :1 AND DATA_HORA = TO_TIMESTAMP(:2, 'YYYY-MM-DD HH24:MI:SS')
                """
                cursor.execute(sql_check, [f"%IBGE: {ibge}%", data_str])
                
                if cursor.fetchone()[0] == 0:
                    orientacao_agricola = obter_recomendacao_agricola(tipo_alerta, nivel)
                    
                    sql_insert = """
                        INSERT INTO CS_ALERTAS (TIPO_ALERTA, ORIGEM_ALERTA, DATA_HORA, CATEGORIA_RISCO, ORIENTACAO, DETALHAMENTO_1, DETALHAMENTO_2)
                        VALUES (:1, 'CEMADEN', TO_TIMESTAMP(:2, 'YYYY-MM-DD HH24:MI:SS'), :3, :4, :5, :6)
                    """
                    det1 = f"Município: {municipio_nome} - UF: {uf_nome} (IBGE: {ibge})"
                    det2 = f"Monitoramento Operacional em Tempo Real"
                    
                    cursor.execute(sql_insert, [tipo_alerta, data_str, nivel, orientacao_agricola, det1, det2])
                    total_registros_processados += 1

        connection.commit()
        mensagem_sucesso = f"Sincronização do Cemaden concluída. {total_registros_processados} novo(s) alerta(s) gravado(s)."
        print(f"   -> {mensagem_sucesso}")
        return "SUCESSO", mensagem_sucesso, total_registros_processados

    except Exception as e:
        connection.rollback()
        mensagem_erro = f"Falha no processo do Cemaden: {str(e)}"
        print(f"[ERRO GERAL] {mensagem_erro}")
        return "ERRO", mensagem_erro, 0
    finally:
        cursor.close()
        connection.close()

if __name__ == "__main__":
    print("--- EXECUTANDO SERVIÇO CEMADEN (CONSOLIDADO ÚNICO) ---")
    sincronizar_escalavel_cemaden()