import datetime
import time
import oracledb
import requests
import sys

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

def buscar_clima_atual(lat, lon):
    """Consome a API de previsão para pegar os dados do momento (current)."""
    url = "https://api.open-meteo.com/v1/forecast"
    tz = "America/Sao_Paulo"
    
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "timezone": tz
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        dados = response.json()
        
        if "current" in dados:
            return dados["current"]
        
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Falha de rede ao buscar dados atuais para Lat: {lat}, Lon: {lon}. Erro: {e}")
    except Exception as e:
        print(f"⚠️ Erro inesperado na API: {e}")
        
    return None

def salvar_leitura_atual(cursor, id_fazenda, current_data):
    """Insere a leitura da hora atual na tabela CS_CLIMA."""
    tempo = current_data.get("time")
    temp = current_data.get("temperature_2m")
    umidade = current_data.get("relative_humidity_2m")
    vento = current_data.get("wind_speed_10m")

    # Prevenção caso a API retorne algum valor nulo
    if temp is None:
        return

    query_insert = """
        INSERT INTO CS_CLIMA (
            ID_FAZENDA, TEMPERATURA, UMIDADE, VELOCIDADE_VENTO, DATA_HORA
        ) VALUES (:1, :2, :3, :4, TO_DATE(:5, 'YYYY-MM-DD"T"HH24:MI'))
    """

    cursor.execute(query_insert, (id_fazenda, temp, umidade, vento, tempo))
    print(f"📡 Leitura registrada -> Fazenda {id_fazenda} | Temp: {temp}°C | Umi: {umidade}% | Vento: {vento} km/h")

def rotina_monitoramento():
    print("🚀 Iniciando Motor de Monitoramento Climático em Tempo Real...")
    print("Pressione [Ctrl+C] a qualquer momento para encerrar de forma segura.\n")
    
    dsn_tns = oracledb.makedsn("oracle.fiap.com.br", 1521, service_name="orcl")
    
    try:
        with oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=dsn_tns) as connection:
            with connection.cursor() as cursor:
                
                # O loop principal que roda a cada 1 hora
                while True:
                    hora_atual = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    print(f"\n[{hora_atual}] 🔄 Iniciando varredura das fazendas...")
                    
                    fazendas = buscar_fazendas(cursor)
                    
                    for id_fazenda, lat, lon in fazendas:
                        dados_atuais = buscar_clima_atual(lat, lon)
                        
                        if dados_atuais:
                            salvar_leitura_atual(cursor, id_fazenda, dados_atuais)
                        
                        # Pequeno respiro para não engargalar a API
                        time.sleep(0.5)
                    
                    connection.commit()
                    print(f"✅ Ciclo concluído. Dados consolidados no Oracle.")
                    
                    # Pausa de 1 hora (3600 segundos) até o próximo ciclo
                    tempo_espera = 3600
                    print(f"⏳ Aguardando 1 hora para a próxima varredura...")
                    time.sleep(tempo_espera)

    except oracledb.DatabaseError as e:
        error_obj, = e.args
        print(f"💥 Erro de Rede/Banco Oracle: {error_obj.message}")
    except Exception as e:
        print(f"💥 Erro inesperado: {e}")

if __name__ == "__main__":
    try:
        rotina_monitoramento()
    except KeyboardInterrupt:
        print("\n\n🛑 [Interrupção Detectada] Encerrando o motor de monitoramento de forma segura. Até logo!")
        sys.exit(0)