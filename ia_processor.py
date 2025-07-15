# ia_processor.py

import os
import google.generativeai as genai
from typing import List
import discord

# Configura a API do Google com a chave do ambiente
try:
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
except TypeError:
    print("AVISO: Chave da API do Google não encontrada. A funcionalidade de IA estará desativada.")
    genai = None

def _format_conversation(messages: List[discord.Message]) -> str:
    """Formata uma lista de mensagens do Discord em um texto único e legível."""
    conversation_text = ""
    for msg in reversed(messages): # As mensagens vêm da mais nova para a mais antiga
        if not msg.author.bot: # Ignora mensagens de bots
            conversation_text += f"{msg.author.display_name}: {msg.clean_content}\n"
    return conversation_text

async def summarize_thread_content(messages: List[discord.Message]) -> str:
    """
    Usa a API do Gemini para resumir uma conversa de um tópico do Discord.
    """
    if not genai:
        return "Erro: A funcionalidade de IA não está configurada (API Key ausente)."

    conversation = _format_conversation(messages)
    if not conversation.strip():
        return "" # Retorna vazio se não houver mensagens de usuários

    # Modelo de IA configurado para ser eficiente e de alta qualidade
    model = genai.GenerativeModel('gemini-1.5-flash-latest')

    # O prompt é a instrução que damos para a IA. É a parte mais importante.
    # REVERTIDO PARA O PROMPT ORIGINAL
    prompt = f"""
    Você é um assistente especialista em resumir discussões de equipes.
    Sua tarefa é ler a transcrição de uma conversa de um tópico do Discord e criar um resumo conciso e informativo em português.

    O resumo deve:
    1.  Ser escrito em da melhor forma para organização.
    2.  Identificar a ideia principal ou o problema discutido.
    3.  Listar os principais pontos, decisões tomadas ou ações sugeridas.
    4.  Incluir quaisquer links importantes que foram compartilhados na conversa.
    5.  Ser objetivo e direto.

    Aqui está a transcrição da conversa:
    ---
    {conversation}
    ---

    Por favor, gere o resumo.
    """

    try:
        response = await model.generate_content_async(prompt)
        return response.text
    except Exception as e:
        print(f"Erro ao chamar a API do Gemini: {e}")
        return f"Erro ao gerar o resumo: {e}"