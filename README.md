# Robo de Copia de Arquivos Excel do ISS

Este robo pega os arquivos Excel da pasta:

`X:\Fiscal\Relatorios Conferencia ISS`

e copia para dentro da pasta oficial dos clientes em:

`X:\Clientes`

## O que ele faz

- le a competencia informada em `MM.AAAA`;
- procura os clientes na origem pelo codigo no nome da pasta, por exemplo `5-CONSULTORIA PLACEHOLDER`;
- procura arquivos `.xlsx` e `.xls` dentro da pasta da competencia, inclusive em subpastas;
- procura o mesmo codigo na pasta `Clientes`;
- dentro do cliente, procura uma pasta com a palavra `fiscal`;
- entra na pasta do ano, por exemplo `2026`;
- se ja existir uma pasta de competencia parecida, usa ela:
  - `07.2026`
  - `072026`
  - `07-2026`
  - `07_2026`
- se nao existir, cria `07.2026`;
- copia os arquivos Excel sem apagar nada da origem;
- nao sobrescreve arquivo diferente com o mesmo nome;
- gera relatorio completo e arquivo de pendencias.

## Como rodar

Simulacao:

```powershell
python robo_copiar_zip_clientes.py --competencia 07.2026 --simular
```

Execucao real:

```powershell
python robo_copiar_zip_clientes.py --competencia 07.2026
```

Modo interativo:

```powershell
python robo_copiar_zip_clientes.py
```

## Saidas

O robo cria uma pasta de relatorios no mesmo lugar onde ele estiver rodando:

- `relatorio_execucao_MMAAAA_datahora.csv`
- `pendencias_MMAAAA_datahora.csv`
- `log_execucao.txt`

## Observacoes importantes

- se nao achar a pasta fiscal do cliente, ele pula e registra pendencia;
- se nao achar o cliente destino pelo codigo, ele pula e registra pendencia;
- se o arquivo ja existir igual, ele marca `JA_EXISTENTE`;
- se o arquivo ja existir com conteudo diferente, ele marca `PENDENCIA_CONFLITO_NOME`.

---

## Configuração

Não há caminho embutido no código: cada escritório monta a rede de um jeito.
Informe os dois, por variável de ambiente ou pela linha de comando.

| variável de ambiente | equivalente na linha de comando | o que é |
|---|---|---|
| `ROBO_ISS_ORIGEM` | `--origem` | pasta dos relatórios de conferência do ISS |
| `ROBO_ISS_DESTINO` | `--destino` | raiz das pastas de cliente |

A linha de comando tem prioridade. Faltando os dois, o robô para com mensagem
clara em vez de procurar numa pasta que não existe.

Para não repetir a cada execução, defina uma vez no Windows:

```powershell
setx ROBO_ISS_ORIGEM "X:\Fiscal\Relatorios Conferencia ISS"
setx ROBO_ISS_DESTINO "X:\Clientes"
```

(troque pelos caminhos da sua rede; abra um terminal novo depois)

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
```

6 testes, todos em pasta temporária: cobrem a leitura da competência nos quatro
formatos aceitos, a busca do cliente pelo código, a cópia sem sobrescrita e a
marcação de conflito quando já existe arquivo diferente com o mesmo nome.

Em produção o robô usa **só a biblioteca padrão** — não há nada a instalar
para rodar.

## Segurança

Este robô não usa credencial nenhuma: trabalha sobre pastas já montadas, e
nunca apaga nada da origem. A pasta de relatórios da execução fica no
`.gitignore` porque nomeia empresas reais.

## Licença

MIT — veja [LICENSE](LICENSE). Use, copie e adapte à vontade.

## Aviso

Este repositório traz **só o código**. Nenhum cadastro de cliente, nenhuma
credencial e nenhum certificado estão aqui, e os caminhos de rede nos exemplos
são genéricos. Os arquivos `*.exemplo.*` existem para o projeto rodar sem
depender de dado real — copie, renomeie e preencha com os seus.
