import datetime
import time
import oracledb
import requests

# ==========================================
# CONFIGURAÇÕES DE CONEXÃO COM O BANCO
# ==========================================
DB_USER = "rm568906"
DB_PASSWORD = "fiap26"
DSN = "oracle.fiap.com.br:1521/orcl"

def buscar_fazendas(cursor):
    """Busca todas as fazendas cadastradas para obter ID, Lat e Long."""
    query = "SELECT ID, LATITUDE, LONGITUDE FROM CS_FAZENDAS"
    cursor.execute(query)
    return cursor.fetchall()

def buscar_dados_historicos(lat, lon):
    """Consome a API dividindo os 10 anos em blocos de 1 ano."""
    # ENDPOINT OFICIAL DE HISTÓRICO CORRIGIDO
    url = "https://archive-api.open-meteo.com/v1/archive"
    tz = "America/Sao_Paulo"
    
    dados_combinados = {
        "hourly": {
            "time": [],
            "temperature_2m": [],
            "relative_humidity_2m": [],
            "wind_speed_10m": []
        }
    }
    
    hoje = datetime.date.today()
    periodos = []
    
    # Gerando os blocos de 1 ano (10 iterações)
    for i in range(10, 0, -1):
        start = hoje - datetime.timedelta(days=365 * i)
        # Se for o último bloco (ano atual), vai até o dia de hoje
        end = hoje - datetime.timedelta(days=365 * (i - 1) + 1) if i > 1 else hoje
        periodos.append((start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
    
    for start, end in periodos:
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start,
            "end_date": end,
            "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m",
            "timezone": tz
        }
        
        try:
            print(f"  -> Baixando período: {start} até {end}...")
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            chunk = response.json()
            
            if "hourly" in chunk:
                dados_combinados["hourly"]["time"].extend(chunk["hourly"]["time"])
                dados_combinados["hourly"]["temperature_2m"].extend(chunk["hourly"]["temperature_2m"])
                dados_combinados["hourly"]["relative_humidity_2m"].extend(chunk["hourly"]["relative_humidity_2m"])
                dados_combinados["hourly"]["wind_speed_10m"].extend(chunk["hourly"]["wind_speed_10m"])
            
            # Respiro amigável para a API
            time.sleep(1)
            
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Falha de rede no bloco {start} até {end} para Lat: {lat}, Lon: {lon}. Erro: {e}")
            return None
        except Exception as e:
            print(f"⚠️ Erro inesperado no processamento do JSON: {e}")
            return None
            
    return dados_combinados

def salvar_no_banco(cursor, id_fazenda, dados_api):
    """Processa o JSON unificado e insere em massa na tabela CS_CLIMA."""
    if not dados_api or "hourly" not in dados_api:
        return

    hourly = dados_api["hourly"]
    tempos = hourly["time"]  
    temperaturas = hourly["temperature_2m"]
    umidades = hourly["relative_humidity_2m"]
    ventos = hourly["wind_speed_10m"]

    query_insert = """
        INSERT INTO CS_CLIMA (
            ID_FAZENDA, TEMPERATURA, UMIDADE, VELOCIDADE_VENTO, DATA_HORA
        ) VALUES (:1, :2, :3, :4, TO_DATE(:5, 'YYYY-MM-DD"T"HH24:MI'))
    """

    dados_lote = []
    for i in range(len(tempos)):
        if temperaturas[i] is None:
            continue

        registro = (id_fazenda, temperaturas[i], umidades[i], ventos[i], tempos[i])
        dados_lote.append(registro)

    if dados_lote:
        cursor.executemany(query_insert, dados_lote)
        print(f"💾 {len(dados_lote)} registros históricos (10 anos) salvos para a Fazenda ID {id_fazenda}.")

def main():
    print("🚀 Iniciando carga histórica de 10 anos estruturada...")
    
    # Gerando a DSN com o Host de DNS limpo da FIAP
    dsn_tns = oracledb.makedsn("oracle.fiap.com.br", 1521, service_name="orcl")
    try:
        with oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=dsn_tns) as connection:
            with connection.cursor() as cursor:
                fazendas = buscar_fazendas(cursor)
                print(f"📍 Encontradas {len(fazendas)} fazendas cadastradas.")

                for id_fazenda, lat, lon in fazendas:
                    print(f"\n🔄 Processando Fazenda ID {id_fazenda} (Lat: {lat}, Lon: {lon})...")
                    
                    dados_meteo = buscar_dados_historicos(lat, lon)
                    if dados_meteo:
                        salvar_no_banco(cursor, id_fazenda, dados_meteo)
                        connection.commit()
                    
                    time.sleep(1)

        print("\n✨ Carga histórica de 10 anos concluída com sucesso no banco!")

    except oracledb.DatabaseError as e:
        error_obj, = e.args
        print(f"💥 Erro de Rede/Banco Oracle (TNS ou Credenciais): {error_obj.message}")
    except Exception as e:
        print(f"💥 Erro inesperado na execução: {e}")

if __name__ == "__main__":
    main()