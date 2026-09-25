-- =====================================================================
-- Consulta Preditiva - Extração de Condições Climáticas (Primeiro Semestre de 2025 - Janela de 3 dias)
-- =====================================================================

SELECT 
    c.DATA_HORA_BR,
    c.TEMPERATURA, 
    c.UMIDADE, 
    c.VELOCIDADE_VENTO, 
    c.PRECIPITACAO,
    e.ID_EVENTO,
    e.CODIGO_IBGE,
    e.UF,
    e.INC_REGIAO,
    e.COBRADE,
    e.INC_GRUPO_DESASTRE,
    e.INC_PEPR_AGRICULTURA,
    e.INC_PEPR_PECUARIA,
    e.INC_TOTAL_PRIVADO,
    e.INC_PE_PLEPR,
    e.INC_PROTOCOLO,
    e.INC_DATA_EVENTO,
    CASE 
        WHEN e.ID_EVENTO IS NOT NULL THEN 1 
        ELSE 0 
    END AS ALERTA_DESASTRE
FROM RM568906.CS_EVENTOS_CLIMA c
INNER JOIN RM568906.CS_EVENTOS e ON c.ID_EVENTO = e.ID_EVENTO
WHERE e.INC_PROTOCOLO IS NOT NULL
  -- Garante estritamente que a data do evento esteja cadastrada
  AND e.INC_DATA_EVENTO IS NOT NULL
  -- Restringe estritamente ao primeiro semestre de 2025 (Janeiro a Junho)
  AND e.INC_DATA_EVENTO >= TO_DATE('2025-01-01', 'YYYY-MM-DD')
  AND e.INC_DATA_EVENTO < TO_DATE('2025-03-31', 'YYYY-MM-DD')
  -- Traz apenas os registros climáticos ocorridos nos últimos 3 dias antes da data do evento
  AND c.DATA_HORA_BR >= (e.INC_DATA_EVENTO - 3)
  AND c.DATA_HORA_BR <= e.INC_DATA_EVENTO
  AND (
       e.COBRADE IS NULL 
       OR SUBSTR(e.COBRADE, 1, 5) IN (
           '11321', -- Deslizamentos
           '12100', -- Inundações
           '12200', -- Enxurradas
           '12300', -- Alagamentos
           '13111', -- Ciclones - Ventos Costeiros
           '13112', -- Ciclones - Mares de Tempestade (Ressacas)
           '13120', -- Frentes Frias/Zonas de Convergência
           '13211', -- Tempestade Local/Convectiva - Tornados
           '13212', -- Tempestade Local/Convectiva - Raios
           '13213', -- Tempestade Local/Convectiva - Granizo
           '13214', -- Tempestade Local/Convectiva - Chuvas Intensas
           '13215', -- Tempestade Local/Convectiva - Vendaval
           '13310', -- Onda de Calor
           '13321', -- Onda de Frio - Friagem
           '13322', -- Onda de Frio - Geadas
           '14110', -- Estiagem
           '14120', -- Seca
           '14131', -- Incêndio Florestal (Parques/APAs)
           '14132', -- Incêndio Florestal (Áreas não protegidas)
           '14140'  -- Baixa Umidade do Ar
       )
  );