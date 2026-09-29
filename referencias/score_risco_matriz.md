# Matriz do Score de Risco (versão ajustada)

O score é **do cliente**. Cada item recebe nota **1 (baixo risco)**, **2 (médio)** ou **3 (alto risco)** e o score
final é a média ponderada das notas pelos pesos (de 1,00 a 3,00). Itens medidos por fazenda (ambientais, climático,
complexidade e procedimentos) viram a nota do cliente pela média das fazendas ponderada pelo valor segurado em cada
uma (fazenda sem equipamento entra com peso igual às demais).

**Classificação:** até 1,66 = **Baixo** | de 1,67 a 2,33 = **Médio** | acima de 2,33 = **Alto**.
**Sem dados** (cliente novo ou indicador ainda sem modelo): nota **2 (neutra)**, sinalizada na tela.

| Categoria | Risco | Descrição | Peso | 1 (baixo) | 2 (médio) | 3 (alto) |
|---|---|---|---|---|---|---|
| Cliente | Exposição da seguradora | Valor total segurado do cliente | 5,0% | Igual ou inferior a R$ 20 milhões | Superior a R$ 20 milhões e igual ou inferior a R$ 60 milhões | Superior a R$ 60 milhões |
| Cliente | Histórico de cumprimento de orientações | Envio de comprovantes de manutenção e ações realizadas que atendem às exigências | 20,0% | Igual ou superior a 80% nos dois critérios | De 50% a menos de 80% em ao menos um dos critérios | Inferior a 50% em ao menos um dos critérios |
| Cliente | Histórico de sinistros | Valor total dos sinistros em relação ao valor segurado | 7,5% | Sem histórico de sinistros | Sinistros iguais ou inferiores a 20% do valor segurado | Sinistros superiores a 20% do valor segurado |
| Ambiental | Queimadas | Histórico de condições favoráveis a queimadas | 10,0% | Risco de queimada na região igual ou inferior a 10% | Superior a 10% e inferior a 30% | Igual ou superior a 30% |
| Ambiental | Hidrológico | Histórico de condições favoráveis a risco hidrológico | 10,0% | Risco hidrológico na região igual ou inferior a 10% | Superior a 10% e inferior a 30% | Igual ou superior a 30% |
| Ambiental | Eventos extremos | Histórico de eventos climáticos extremos no município | 2,5% | Risco de eventos extremos igual ou inferior a 10% | Superior a 10% e inferior a 30% | Igual ou superior a 30% |
| Operacional | Climático | Condições climáticas que causam dano ao equipamento (atolamento, deslizamento) | 5,0% | Risco igual ou inferior a 10% | Superior a 10% e inferior a 30% | Igual ou superior a 30% |
| Operacional | Complexidade da manutenção | Recomendações de manutenção em 5 anos, **por equipamento** | 20,0% | Igual ou inferior a 20 por equipamento | Superior a 20 e inferior a 40 por equipamento | Igual ou superior a 40 por equipamento |
| Operacional | Procedimentos de manutenção | Maturidade no questionário (nota de 0 a 15, convertida em %) | 20,0% | Maturidade igual ou superior a 80% | Superior a 50% e inferior a 80% | Igual ou inferior a 50% |

Soma dos pesos: 5 + 20 + 7,5 + 10 + 10 + 2,5 + 5 + 20 + 20 = **100%**.

## O que mudou em relação à versão original

1. **Procedimentos de manutenção: escala invertida corrigida.** Maturidade alta passa a ser risco baixo (nota 1).
2. **Histórico de cumprimento: faixas sem buraco.** Antes, valores entre 50% e 51% não caíam em nenhuma faixa.
3. **Cliente novo recebe nota 2 (neutra)** em histórico de cumprimento e de sinistros, em vez de 3. Antes, só por
   ser novo, o cliente acumulava 27,5% do peso na nota máxima de risco.
4. **Complexidade da manutenção medida por equipamento** (e não pelo total do cliente, que já pesa em Exposição) e
   com peso reduzido de 25% para 20%. Os 5 pontos passaram para Procedimentos de manutenção (15% -> 20%), que mede o
   comportamento do cliente.
5. **Score final e classificação definidos** (média ponderada de 1 a 3; Baixo, Médio e Alto), calculado por cliente.
6. **Faixas de exposição compatíveis com grandes grupos:** até R$ 20 milhões, até R$ 60 milhões e acima (antes
   R$ 500 mil e R$ 1,5 milhão, valores que uma única colheitadeira já ultrapassa).
7. Correção de digitação ("manutençã", "na. região", "equipament", "manutençãoigual").

## Como cada item será medido (proposta para a implementação)

| Item | Fonte | Cálculo |
|---|---|---|
| Exposição | CS_EQUIPAMENTOS_SEGURADOS | Soma do valor segurado dos equipamentos ativos do cliente |
| Cumprimento de orientações | CS_MANUTENCOES_REALIZADAS | % com comprovante entregue e % que atende aos critérios (manutenções já vencidas); vale o menor dos dois |
| Sinistros | CS_SINISTROS | Soma das indenizações / valor segurado |
| Queimadas | CS_ALERTAS (INPE) | % dos dias do último ano com foco a até 15 km da fazenda em 3 dias (mesmo evento do modelo de previsão) |
| Hidrológico | CS_ALERTAS (CEMADEN) | % dos dias do último ano com alerta no município (prévia até o modelo hidrológico ficar pronto) |
| Eventos extremos | CS_EVENTOS (S2iD) | % dos anos da base com desastre registrado no município |
| Climático (equipamento) | a definir | Indicador de atolamento e deslizamento (ainda não construído): nota 2 até lá |
| Complexidade da manutenção | CS_EQUIPAMENTOS_ORIENTACOES | Recomendações do manual de cada modelo em 5 anos, média por equipamento |
| Procedimentos de manutenção | CS_SCORE_MANUTENCAO_PROCEDIMENTOS | Última avaliação: SCORE_FINAL / 15 |

As notas de cada item e o score final serão gravados em CS_SCORE_GESTAO (uma linha por fazenda e cálculo).
