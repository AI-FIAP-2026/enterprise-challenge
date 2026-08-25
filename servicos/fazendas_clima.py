# -*- coding: utf-8 -*-
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

requests = __import__('requests')
import datetime
import time
import oracledb
from auth import USER, PASSWORD, DSN

def atualizar_dados_clima_fazendas():
    print("\n[CLIMA FAZENDAS] Iniciando verificação e atualização horária (CS_FAZENDAS_CLIMA - Padrão UTC)...")
    
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    cursor = connection.cursor()
    total_geral_inserido = 0
    
    try:
        cursor.execute("SELECT ID, NOME_FAZENDA, LATITUDE, LONGITUDE FROM CS_FAZENDAS")
        fazendas = cursor.fetchall()
        
        if not fazendas:
            msg = "Nenhuma fazenda encontrada na base."
            print(f"[CLIMA FAZENDAS] {msg}")
            return "ALERTA", msg, 0

        fuso_utc = datetime.timezone.utc
        agora_utc = datetime.datetime.now(fuso_utc).replace(tzinfo=None)
        quatro_anos_atras = agora_utc - datetime.timedelta(days=365 * 4)

        for fazenda in fazendas:
            id_fazenda, nome_fazenda, lat, lon = fazenda
            print(f"\n--------------------------------------------------")
            print(f"Processando fazenda: {nome_fazenda} (ID: {id_fazenda})")
            print(f"--------------------------------------------------")
            
            cursor.execute("SELECT MAX(DATA_HORA) FROM CS_FAZENDAS_CLIMA WHERE ID_FAZENDA = :1", (id_fazenda,))
            resultado = cursor.fetchone()
            ultima_data_banco = resultado[0] if resultado else None
            
            if ultima_data_banco is None:
                data_inicio = quatro_anos_atras
                print(f" -> Nenhum registro anterior encontrado.")
                print(f" -> Ação: Iniciando carga histórica a partir de {data_inicio.strftime('%d/%m/%Y %H:%M')} (UTC)...")
            else:
                data_inicio = ultima_data_banco + datetime.timedelta(hours=1)
                ultima_data_brasilia = ultima_data_banco - datetime.timedelta(hours=3)
                ultima_data_formatada = ultima_data_brasilia.strftime('%d/%m/%Y às %H:%M')
                
                if data_inicio >= agora_utc:
                    print(f" -> Status: Dados já estão 100% atualizados.")
                    print(f" -> Detalhe: O banco já possui registros até a última hora disponível ({ultima_data_formatada} - Horário Local).")
                    continue
                
                print(f" -> Status: Dados atualizados no banco até {ultima_data_formatada} (Horário Local).")
                print(f" -> Ação: Buscando novos dados horários...")

            data_inicio_str = data_inicio.strftime("%Y-%m-%d")
            data_fim_str = agora_utc.strftime("%Y-%m-%d")
            
            url = "https://archive-api.open-meteo.com/v1/archive"
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": data_inicio_str,
                "end_date": data_fim_str,
                "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation"
            }
            
            response = None
            for tentativa in range(3):
                try:
                    response = requests.get(url, params=params, timeout=30)
                    if response.status_code == 200:
                        break
                    elif response.status_code == 429:
                        print(f"   [AVISO] Rate limit atingido. Pausando por 20 segundos...")
                        time.sleep(20)
                    else:
                        time.sleep(3)
                except Exception:
                    time.sleep(3)
            
            if not response or response.status_code != 200:
                print(f"   [ERRO API] Falha ao buscar dados para a fazenda {nome_fazenda}.")
                continue
                
            dados = response.json()
            hourly = dados.get("hourly", {})
            tempos = hourly.get("time", [])
            temperaturas = hourly.get("temperature_2m", [])
            umidades = hourly.get("relative_humidity_2m", [])
            ventos = hourly.get("wind_speed_10m", [])
            precipitacoes = hourly.get("precipitation", [])
            
            if not tempos:
                print(f"   -> Nenhum registro retornado pela API para o período.")
                continue

            lote_dados = []
            for i in range(len(tempos)):
                data_str = tempos[i].replace("T", " ") + ":00"
                data_hora = datetime.datetime.strptime(data_str, "%Y-%m-%d %H:%M:%S")
                
                if ultima_data_banco is not None and data_hora <= ultima_data_banco:
                    continue
                if data_hora > agora_utc:
                    break

                temp = temperaturas[i] if i < len(temperaturas) and temperaturas[i] is not None else 0.0
                umid = umidades[i] if i < len(umidades) and umidades[i] is not None else 0.0
                vento = ventos[i] if i < len(ventos) and ventos[i] is not None else 0.0
                precip = precipitacoes[i] if i < len(precipitacoes) and precipitacoes[i] is not None else 0.0

                lote_dados.append((
                    id_fazenda, 
                    float(temp), 
                    float(umid), 
                    float(vento), 
                    float(precip),
                    data_hora.strftime('%Y-%m-%d %H:%M:%S')
                ))

            if lote_dados:
                sql_insert = """
                    INSERT INTO CS_FAZENDAS_CLIMA (ID_FAZENDA, TEMPERATURA, UMIDADE, VELOCIDADE_VENTO, PRECIPITACAO, DATA_HORA)
                    VALUES (:1, :2, :3, :4, :5, TO_TIMESTAMP(:6, 'YYYY-MM-DD HH24:MI:SS'))
                """
                cursor.executemany(sql_insert, lote_dados)
                connection.commit()
                total_geral_inserido += len(lote_dados)
                print(f"   -> Sucesso: {len(lote_dados)} novos registros horários inseridos (UTC).")
            else:
                print(f"   -> Nenhuma nova hora pendente para inserir.")

            time.sleep(1)

        msg_sucesso = f"Atualização climática das fazendas concluída com sucesso. {total_geral_inserido} registros inseridos."
        print(f"\n[CLIMA FAZENDAS] {msg_sucesso}")
        return "SUCESSO", msg_sucesso, total_geral_inserido

    except Exception as e:
        connection.rollback()
        msg_erro = f"Ocorreu um erro durante a atualização climática das fazendas: {str(e)}"
        print(f"[ERRO GERAL] {msg_erro}")
        return "ERRO", msg_erro, 0
    finally:
        cursor.close()
        connection.close()

if __name__ == "__main__":
    print("--- EXECUTANDO CLIMA FAZENDAS (Isolada) ---")
    atualizar_dados_clima_fazendas()