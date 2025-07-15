# bot.py (Versão com importação corrigida)

import discord
from discord import app_commands, Interaction, SelectOption, Color
from discord.ext import commands
from discord.ui import Select, View # <-- CORREÇÃO: Importação do local correto
import os
from dotenv import load_dotenv
from typing import Optional
import asyncio 
# Módulos locais
from notion_integration import NotionIntegration, NotionAPIError
from config_utils import save_config, load_config
from ui_components import (
    SelectView,
    PaginationView,
    SearchModal,
    CardModal,
    ManagementView,
)
from webhook_server import run_server

# Carregar variáveis de ambiente e inicializar bot/notion
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)
notion = NotionIntegration()


# --- FUNÇÃO AUXILIAR DE CONFIGURAÇÃO ---

async def run_full_config_flow(interaction: Interaction, url: str, is_update: bool = False):
    """Executa o fluxo completo de configuração de um canal."""
    config_channel = interaction.channel
    if isinstance(interaction.channel, discord.Thread):
        config_channel = interaction.channel.parent
    config_channel_id = config_channel.id

    try:
        # Salva a URL inicial para garantir que o canal é reconhecido como configurado
        save_config(interaction.guild_id, config_channel_id, {'notion_url': url})

        all_properties = notion.get_properties_for_interaction(url)
        property_names = [prop['name'] for prop in all_properties]

        async def run_selection_process(prompt_title, prompt_description, original_interaction):
            class MultiSelect(Select):
                def __init__(self):
                    opts = [SelectOption(label=name) for name in property_names[:25]]
                    super().__init__(placeholder="Escolha as propriedades...", min_values=1, max_values=len(opts), options=opts)

                async def callback(self, inter: Interaction):
                    self.view.result = self.values
                    for item in self.view.children: item.disabled = True
                    await inter.response.edit_message(content=f"Seleção para '{prompt_title}' confirmada!", view=self.view)
                    self.view.stop()

            view = SelectView(MultiSelect(), author_id=original_interaction.user.id, timeout=300.0)
            await original_interaction.followup.send(embed=discord.Embed(title=prompt_title, description=prompt_description, color=Color.blue()), view=view, ephemeral=True)
            await view.wait()
            return getattr(view, 'result', None)

        # Configurar propriedades de CRIAÇÃO
        create_props = await run_selection_process("🛠️ Configurar Criação (`/card`)", "Selecione as propriedades que o bot deve perguntar ao criar um card.", interaction)
        if create_props is None:
            return await interaction.followup.send("⌛ Configuração cancelada. O processo não foi concluído.", ephemeral=True)
        save_config(interaction.guild_id, config_channel_id, {'create_properties': create_props})
        await interaction.followup.send(f"✅ Propriedades para **criação** salvas: `{', '.join(create_props)}`", ephemeral=True)

        # Configurar propriedades de EXIBIÇÃO
        display_props = await run_selection_process("🎨 Configurar Exibição (`/busca`)", "Selecione as propriedades que o bot deve mostrar nos resultados da busca e embeds.", interaction)
        if display_props is None:
            return await interaction.followup.send("⌛ Configuração cancelada. O processo não foi concluído.", ephemeral=True)
        save_config(interaction.guild_id, config_channel_id, {'display_properties': display_props})

        # Se for uma nova configuração, define valores padrão
        if not is_update:
            save_config(interaction.guild_id, config_channel_id, {
                'action_buttons_enabled': True,
                'topic_link_property_name': None,
                'individual_person_prop': None,
                'collective_person_prop': None
            })

        await interaction.followup.send(f"✅ Propriedades para **exibição** salvas: `{', '.join(display_props)}`", ephemeral=True)
        await interaction.followup.send(f"🎉 **Configuração para o canal `#{config_channel.name}` concluída com sucesso!**", ephemeral=True)

    except NotionAPIError as e:
        await interaction.followup.send(f"❌ **Erro ao acessar o Notion:**\n`{e}`\n\nA configuração não pôde ser concluída. Verifique a URL e as permissões do Bot na sua integração do Notion.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"🔴 **Ocorreu um erro inesperado durante a configuração:**\n`{e}`", ephemeral=True)
        print(f"Erro inesperado no /config flow: {e}")


# --- EVENTOS DO BOT ---

@bot.event
async def on_ready():
    """Evento disparado quando o bot está pronto."""
    if DISCORD_GUILD_ID:
        guild = discord.Object(id=DISCORD_GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Comandos sincronizados para o servidor {DISCORD_GUILD_ID}.")
    else:
        await bot.tree.sync()
        print("Comandos sincronizados globalmente.")
        
    # Inicia o servidor de webhook quando o bot estiver pronto
    loop = asyncio.get_event_loop()
    run_server(bot, loop)

    print(f"✅ {bot.user} está online e pronto para uso!")

# --- COMANDOS DE BARRA (/) ---

@bot.tree.command(name="config", description="(Admin) Configura ou gerencia o bot para este canal.")
@app_commands.describe(url="Opcional: URL da base de dados do Notion para configurar ou reconfigurar.")
@app_commands.checks.has_permissions(administrator=True)
async def config_command(interaction: Interaction, url: Optional[str] = None):
    await interaction.response.defer(ephemeral=True, thinking=True)

    channel_id = interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else interaction.channel.id
    config = load_config(interaction.guild_id, channel_id)

    if url:
        if not notion.extract_database_id(url):
            return await interaction.followup.send("❌ A URL do Notion fornecida parece ser inválida. Verifique se é a URL de uma base de dados.", ephemeral=True)

        await interaction.followup.send("Iniciando a configuração/reconfiguração completa...", ephemeral=True)
        await run_full_config_flow(interaction, url, is_update=bool(config))
        return

    if config and 'notion_url' in config:
        view = ManagementView(interaction, notion, config)
        await interaction.followup.send("Este canal já está configurado. Escolha uma opção de gerenciamento:", view=view, ephemeral=True)
    else:
        await interaction.followup.send("❌ Este canal ainda não foi configurado. Use `/config` e forneça a URL da sua base de dados do Notion.", ephemeral=True)

@config_command.error
async def config_command_error(interaction: Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ Você precisa ser um administrador para usar este comando."
    else:
        message = f"🔴 Um erro de comando ocorreu: {error}"
        print(f"Erro no comando /config: {error}")
        
    try:
        await interaction.followup.send(message, ephemeral=True)
    except discord.errors.HTTPException as e:
        print(f"Não foi possível enviar a mensagem de erro via followup: {e}")



@bot.tree.command(name="card", description="Abre um formulário para criar um novo card no Notion.")
async def interactive_card(interaction: Interaction):
    try:
        config_channel_id = interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else interaction.channel.id
        config = load_config(interaction.guild_id, config_channel_id)

        if not config or 'notion_url' not in config:
            return await interaction.response.send_message("❌ O Notion ainda não foi configurado para este canal. Peça para um admin usar `/config`.", ephemeral=True)

        all_properties = notion.get_properties_for_interaction(config['notion_url'])

        thread_context = interaction.channel if isinstance(interaction.channel, discord.Thread) else None
        topic_title = thread_context.name if thread_context else None

        create_properties_names = config.get('create_properties', []).copy()

        # Remove propriedades que são preenchidas automaticamente
        props_to_remove = [
            config.get('topic_link_property_name'),
            config.get('individual_person_prop'),
            config.get('collective_person_prop')
        ]
        create_properties_names = [p for p in create_properties_names if p and p not in props_to_remove]

        if not create_properties_names:
            return await interaction.response.send_message("❌ Nenhuma propriedade foi configurada para criação manual de cards. Use `/config` para ajustar.", ephemeral=True)

        properties_to_ask = [prop for prop in all_properties if prop['name'] in create_properties_names]
        text_props = [p for p in properties_to_ask if p['type'] not in ['select', 'multi_select', 'status']]
        select_props = [p for p in properties_to_ask if p['type'] in ['select', 'multi_select', 'status']]

        # Validação da quantidade de campos
        if len(text_props) > 5: return await interaction.response.send_message(f"❌ Formulário com muitos campos de texto ({len(text_props)}). O máximo é 5.", ephemeral=True)
        if len(select_props) > 4: return await interaction.response.send_message(f"❌ Formulário com muitos menus de seleção ({len(select_props)}). O máximo é 4.", ephemeral=True)

        modal = CardModal(
            notion=notion,
            config=config,
            all_properties=all_properties,
            text_props=text_props,
            select_props=select_props,
            thread_context=thread_context,
            topic_title=topic_title
        )
        await interaction.response.send_modal(modal)

    except Exception as e:
        error_message = f"🔴 Erro inesperado ao iniciar o comando `/card`: {e}"
        print(error_message)
        if not interaction.response.is_done():
            await interaction.response.send_message(error_message, ephemeral=True)


@bot.tree.command(name="busca", description="Busca ou edita um card no Notion.")
async def interactive_search(interaction: Interaction):
    try:
        config_channel_id = interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else interaction.channel.id
        config = load_config(interaction.guild_id, config_channel_id)
        if not config or 'notion_url' not in config:
            return await interaction.response.send_message("❌ O Notion não foi configurado para este canal. Use `/config`.", ephemeral=True)

        all_properties = notion.get_properties_for_interaction(config['notion_url'])
        display_properties_names = config.get('display_properties', [])
        if not display_properties_names:
            return await interaction.response.send_message("❌ As propriedades para busca não foram configuradas. Use `/config`.", ephemeral=True)

        searchable_options = [prop for prop in all_properties if prop['name'] in display_properties_names]
        if not searchable_options:
            return await interaction.response.send_message("❌ Nenhuma propriedade pesquisável configurada.", ephemeral=True)

        class PropertySelect(Select):
            def __init__(self, searchable_props, author_id):
                self.searchable_props = searchable_props
                self.author_id = author_id
                opts = [SelectOption(label=p['name'], description=f"Tipo: {p['type']}") for p in self.searchable_props[:25]]
                super().__init__(placeholder="Escolha uma propriedade para pesquisar...", options=opts)

            async def callback(self, inter: Interaction):
                if inter.user.id != self.author_id:
                    return await inter.response.send_message("Você não pode interagir com o menu de outra pessoa.", ephemeral=True)

                selected_prop_name = self.values[0]
                selected_property = next((p for p in all_properties if p['name'] == selected_prop_name), None)

                if selected_property['type'] in ['select', 'multi_select', 'status']:
                    prop_options = selected_property.get('options', [])

                    class OptionSelect(Select):
                        def __init__(self):
                            opts = [SelectOption(label=opt) for opt in prop_options[:25]]
                            super().__init__(placeholder=f"Escolha uma opção de '{selected_property['name']}'...", options=opts)

                        async def callback(self, sub_inter: Interaction):
                            await sub_inter.response.defer(thinking=True, ephemeral=True)
                            search_term = self.values[0]
                            cards = notion.search_in_database(config['notion_url'], search_term, selected_property['name'], selected_property['type'])
                            results = cards.get('results', [])
                            if not results:
                                return await sub_inter.followup.send(f"❌ Nenhum resultado para '{search_term}'.", ephemeral=True)

                            await sub_inter.followup.send(f"✅ {len(results)} resultado(s) encontrado(s)!", ephemeral=True)

                            view = PaginationView(sub_inter.user, results, config, notion, actions=['edit', 'delete', 'share'])
                            view.update_nav_buttons()
                            await sub_inter.followup.send(embed=await view.get_page_embed(), view=view, ephemeral=True)

                    view_options = View(timeout=120.0)
                    view_options.add_item(OptionSelect())
                    await inter.response.edit_message(content=f"➡️ Escolha um valor para **{selected_property['name']}**:", view=view_options)
                else:
                    await inter.response.send_modal(SearchModal(notion=notion, config=config, selected_property=selected_property))

        initial_view = View(timeout=180.0)
        initial_view.add_item(PropertySelect(searchable_options, interaction.user.id))
        await interaction.response.send_message("🔎 Escolha no menu abaixo a propriedade para sua busca.", view=initial_view, ephemeral=True)

    except NotionAPIError as e:
        msg = f"❌ Erro com o Notion: {e}"
        if not interaction.response.is_done(): await interaction.response.send_message(msg, ephemeral=True)
        else: await interaction.followup.send(msg, ephemeral=True)
    except Exception as e:
        msg = f"🔴 Erro inesperado: {e}"
        if not interaction.response.is_done(): await interaction.response.send_message(msg, ephemeral=True)
        else: await interaction.followup.send(msg, ephemeral=True)
        print(f"Erro inesperado no /busca: {e}")


@bot.tree.command(name="num_cards", description="Mostra o total de cards no banco de dados do canal.")
async def num_cards(interaction: Interaction):
    try:
        config_channel_id = interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else interaction.channel.id
        config = load_config(interaction.guild_id, config_channel_id)
        if not config or 'notion_url' not in config:
            return await interaction.response.send_message("❌ O Notion não foi configurado para este canal. Use `/config`.", ephemeral=True)
        count = notion.get_database_count(config['notion_url'])
        await interaction.response.send_message(f"📊 O banco de dados deste canal contém **{count}** cards.")
    except NotionAPIError as e:
        await interaction.response.send_message(f"❌ Erro ao acessar o Notion: {e}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"🔴 Erro inesperado: {e}", ephemeral=True)
        print(f"Erro inesperado no /num_cards: {e}")


# --- INICIAR O BOT ---
if __name__ == "__main__":
    if DISCORD_TOKEN:
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(f"❌ Erro fatal ao iniciar o bot: {e}")
    else:
        print("❌ Token do Discord (DISCORD_TOKEN) não encontrado no arquivo .env")
