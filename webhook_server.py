# webhook_server.py

from flask import Flask, request, jsonify
from threading import Thread
import asyncio
import re
import os
from notion_integration import NotionIntegration, NotionAPIError
from config_utils import load_config
import discord

# Inicializa o Flask
app = Flask(__name__)

# Variáveis globais para acessar o bot e o loop de eventos
BOT_INSTANCE = None
BOT_LOOP = None

def extract_thread_id_from_url(url: str) -> int | None:
    """Extrai o ID do tópico/canal de uma URL do Discord."""
    if not url:
        return None
    # A URL de um tópico é discord.com/channels/GUILD_ID/THREAD_ID
    match = re.search(r'discord.com/channels/\d+/(\d+)', url)
    if match:
        return int(match.group(1))
    return None

async def process_webhook_and_notify(data):
    """
    Função assíncrona que processa o payload do webhook e envia a notificação.
    """
    if not BOT_INSTANCE:
        print("Webhook recebido, mas a instância do bot não está pronta.")
        return

    try:
        # A Notion API pode enviar diferentes tipos de payload.
        # Vamos nos concentrar em 'page' que é o mais comum para atualizações.
        if 'page' not in data:
            print("Webhook recebido sem dados da página.")
            return

        page_data = data['page']
        page_id = page_data.get('id')
        if not page_id:
            return

        # Para obter a config, precisamos primeiro do link do tópico,
        # que nos dará o guild_id e channel_id.
        notion = NotionIntegration()
        
        # O webhook pode não conter todas as propriedades, então fazemos um retrieve
        full_page = notion.get_page(page_id)
        
        # Encontrar a propriedade que guarda o link do tópico
        # Precisamos iterar sobre as configs para encontrar a correta
        # Nota: Esta parte é complexa, pois não sabemos o guild/channel de antemão.
        # A abordagem mais robusta é encontrar a propriedade de URL e extrair dela.
        
        thread_url = None
        topic_prop_name = None
        guild_id = None

        # Como não sabemos a guild, não podemos carregar a config diretamente.
        # Primeiro, precisamos encontrar a URL do discord na página.
        for prop_name, prop_value in full_page.get("properties", {}).items():
            if prop_value.get("type") == "url" and prop_value["url"] and "discord.com/channels" in prop_value["url"]:
                thread_url = prop_value["url"]
                # Extrai o guild_id da URL para carregar a config correta
                match = re.search(r'discord.com/channels/(\d+)', thread_url)
                if match:
                    guild_id = int(match.group(1))
                break

        if not thread_url or not guild_id:
            print(f"Webhook para page {page_id} recebido, mas não foi encontrado um link de tópico do Discord válido.")
            return
            
        thread_id = extract_thread_id_from_url(thread_url)
        if not thread_id:
            return

        # Agora tentamos buscar o tópico para obter o channel_id e carregar a config
        thread = await BOT_INSTANCE.fetch_channel(thread_id)
        config_channel_id = thread.parent_id
        
        config = load_config(guild_id, config_channel_id)
        if not config:
            print(f"Configuração para guild {guild_id} e canal {config_channel_id} não encontrada.")
            return

        # Formata um embed com as informações do card atualizado
        display_properties = config.get('display_properties', [])
        embed = notion.format_page_for_embed(full_page, display_properties)
        if embed:
            embed.title = f"🔔 Card Atualizado: {embed.title.replace('📌 ', '')}"
            embed.color = discord.Color.orange()
            embed.description = "Uma automação do Notion foi disparada para este card."
            
            await thread.send(embed=embed)

    except NotionAPIError as e:
        print(f"Erro de API do Notion ao processar webhook: {e}")
    except Exception as e:
        print(f"Erro inesperado ao processar webhook: {e}")


@app.route('/notion-webhook', methods=['POST'])
def notion_webhook_receiver():
    """
    Endpoint que recebe a notificação do Notion.
    """
    # Notion pode enviar um 'challenge' para verificar a URL
    if request.headers.get('X-Notion-Webhook-Challenge'):
        challenge = request.headers.get('X-Notion-Webhook-Challenge')
        print(f"Respondendo ao desafio do Notion com: {challenge}")
        return challenge, 200

    if not BOT_LOOP:
        return jsonify({"error": "Bot event loop not ready"}), 503

    # Dispara a função assíncrona em um ambiente thread-safe
    data = request.json
    asyncio.run_coroutine_threadsafe(process_webhook_and_notify(data), BOT_LOOP)
    
    return jsonify({"status": "received"}), 200


def run_server(bot_instance, bot_loop):
    """
    Inicia o servidor Flask em uma thread separada.
    """
    global BOT_INSTANCE, BOT_LOOP
    BOT_INSTANCE = bot_instance
    BOT_LOOP = bot_loop
    
    # Use '0.0.0.0' para ser acessível externamente (necessário para o Render)
    # A porta é geralmente definida por uma variável de ambiente pela plataforma de hospedagem
    port = int(os.environ.get('PORT', 8080))
    thread = Thread(target=lambda: app.run(host='0.0.0.0', port=port, debug=False))
    thread.daemon = True
    thread.start()
    print(f"🚀 Servidor de Webhook iniciado em http://0.0.0.0:{port}")
