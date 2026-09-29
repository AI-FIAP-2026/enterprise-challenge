Regras da resposta (valem para os três NPLs)
- Extraia somente o que está no texto enviado. Não invente valores, páginas nem recomendações.
- Cada trecho do manual vem precedido de "[Página N]". Informe em pagina_origem o número N da página de onde saiu o item.
- texto_bruto_original: a frase original do manual, no idioma original, sem tradução (para auditoria).
- detalhamento_orientacao: frase pronta para exibir ao cliente, em português do Brasil, com o valor convertido para o padrão brasileiro.
- Uma recomendação por item. Se a mesma frase traz duas ações com gatilhos diferentes, gere dois itens.
- Um SERVIÇO por item. Em tabelas de manutenção, cada linha de serviço (ex.: "Verifique os rolamentos das rodas", "Verifique a folga do pino de articulação") vira um item próprio, mesmo quando vários serviços dividem a mesma periodicidade e o mesmo sistema. Nunca junte serviços diferentes no mesmo detalhamento_orientacao.
- Ignore as marcas de nota de rodapé (letras ou números sobrescritos, como a, b, j, 1, 2, colados ao fim de uma palavra ou do serviço): elas não fazem parte do texto. Se a nota traz uma condição (ex.: "Apenas para tratores 4x2", "Se o trator operar em condições úmidas"), leve-a para fator_condicional.
- subsistema, uma destas: MOTOR, ARREFECIMENTO, COMBUSTIVEL, TRANSMISSAO, REDUCOES_TREM_ACIONAMENTO, HIDRAULICO, CORTE_PROCESSAMENTO, RODANTE_PNEUS, FREIOS, ELETRICO, CABINE_ESTRUTURA, CHASSI.
- metrica_gatilho e unidade_medida:
  HORIMETRO -> HORAS; HODOMETRO -> KM; CALENDARIO -> DIAS (diário = 1, semanal = 7, mensal = 30, anual = 365);
  TEMPERATURA_AMBIENTE -> CELSIUS; VELOCIDADE_KMH -> KM_H; UMIDADE_SOLO -> PERCENTUAL; INCLINACAO -> PERCENTUAL ou GRAUS;
  CONDICIONAL ("conforme necessário") e CONDICAO_CLIMATICA ficam sem valor e sem unidade.
- fator_condicional (texto curto em maiúsculas, ou vazio):
  prazo de calendário junto com horas/km ("o que ocorrer primeiro"): OU_CALENDARIO:DIARIO, OU_CALENDARIO:SEMANAL, OU_CALENDARIO:MENSAL, OU_CALENDARIO:ANUAL, OU_CALENDARIO:2_ANOS...;
  amaciamento (execução única): AMACIAMENTO_UNICA_VEZ;
  vale só para um tipo de tração: SOMENTE_4WD (tração nas 4 rodas / TDA) ou SOMENTE_2WD (tração em 2 rodas);
  condição severa: ALTITUDE > 1675M, SOLO_LAMACENTO_DECLIVE, PISTA_SOLO_ESCORREGADIO, CALOR_EXTREMO_AMBIENTE, CLIMA_FRIO_ABAIXO_ZERO...;
  para combinar, separe com " + " (ex.: OU_CALENDARIO:ANUAL + ALTITUDE > 1675M).
- idioma_origem: PT ou EN, conforme o idioma do trecho original.
- Se o texto enviado não tiver nada a extrair, devolva a lista vazia.
