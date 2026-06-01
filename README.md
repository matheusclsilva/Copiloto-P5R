# Copiloto P5R (Platinum Assistant)

O **Copiloto P5R** é um assistente inteligente e automatizado para o jogo *Persona 5 Royal* (versão PC). Seu objetivo é ajudar o jogador a conquistar os 100% e a Platina em uma única jogada, acompanhando ativamente o seu progresso e cruzando-o com o "Perfect Schedule" do site PSNProfiles.

## 🚀 Funcionalidades Principais

- **Leitura em Tempo Real:** Lê a memória do jogo (`P5R.exe`) e os arquivos de *save* de forma segura (somente leitura) para descobrir em qual dia do jogo você está e quais os seus atributos (Social Stats, nível de Confidant, Yen, etc.).
- **Guia Passo a Passo Automático:** Baseado no dia in-game atual, o assistente te diz exatamente o que deve ser feito durante a Manhã/Tarde e Noite.
- **Alertas de Missáveis (Perdíveis):** Avisa sobre eventos e ações que estão prestes a expirar, com urgência medida em dias.
- **Inteligência Artificial Integrada:** Utiliza a API da DeepSeek para processar o seu estado atual em relação ao guia e gerar sugestões táticas personalizadas.
- **Interface Flutuante (GUI):** Acompanha seu jogo através de uma pequena janela no PC, sem precisar de telas extras.

## ⚙️ Pré-requisitos e Instalação

1. Certifique-se de ter o Python instalado.
2. Instale as dependências executando:
   ```bash
   pip install -r requirements.txt
   ```
3. Edite o arquivo `config.json` para adicionar a sua chave da API da DeepSeek:
   ```json
   {
     "deepseek_api_key": "Sua-Chave-DeepSeek-Aqui",
     ...
   }
   ```

## 🎮 Como Usar

Abra o terminal na pasta do projeto e execute um dos comandos abaixo:

- **Iniciar com Interface Gráfica (Recomendado):**
  ```bash
  python main.py
  ```
  Isso abrirá a janela flutuante e o ícone na bandeja do sistema.

- **Diagnóstico do Sistema:**
  ```bash
  python main.py --check
  ```
  Ideal para testar se o programa está conseguindo ler o processo do jogo e os arquivos de save.

- **Modo Terminal / Headless:**
  ```bash
  python main.py --headless
  ```
  Executa a rotina uma única vez e imprime o seu estado atual e as sugestões no terminal.

## ⚠️ Aviso
Esta aplicação **não modifica** seu arquivo de save e **não interfere** no executável do jogo de forma intrusiva. Ela trabalha unicamente em modo de leitura (Read-Only) para garantir a segurança da sua progressão.
