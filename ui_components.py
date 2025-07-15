# ui_components.py (Versão completa com funcionalidade de IA e Anexos e formatação corrigida)

import discord
from discord import Interaction, SelectOption, ButtonStyle, Color
from discord.ui import View, Button, Select
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime

# Módulos locais
from notion_integration import NotionIntegration, NotionAPIError
from config_utils import save_config
from ia_processor import summarize_thread_content

# --- FUNÇÕES AUXILIARES DE UI ---

async def get_topic_participants(thread: discord.Thread, limit: int = 100) -> set[discord.Member]:
    """Busca os participantes únicos de um tópico com base no histórico de mensagens."""
    participants = set()
    async for message in thread.history(limit=limit):
        if not message.author.bot:
            participants.add(message.author)
    return participants

async def get_thread_attachments(thread: discord.Thread, limit: int = 100) -> List[Dict[str, str]]:
    """
    Busca URLs de anexos de imagens, GIFs e vídeos em um tópico.
    Retorna uma lista de dicionários com 'type' e 'url'.
    """
    attachments_data = []
    async for message in thread.history(limit=limit):
        if message.attachments:
            for attachment in message.attachments:
                # Verifica se o tipo de arquivo é uma imagem, vídeo ou gif
                # (Discord trata GIFs como imagens, mas verificamos a extensão por garantia)
                if attachment.content_type.startswith(('image/', 'video/')) or attachment.filename.lower().endswith(('.gif')):
                    attachments_data.append({
                        "type": attachment.content_type.split('/')[0], # 'image' ou 'video'
                        "url": attachment.url,
                        "filename": attachment.filename # Pode ser útil para depuração ou nomear o anexo no Notion
                    })
    return attachments_data


async def _build_notion_page_content(config: dict, thread_context: Optional[discord.Thread], notion_integration: NotionIntegration) -> Optional[List[Dict]]:
    """
    Verifica a config, busca histórico/anexos, gera resumo e coleta anexos,
    retornando o payload completo de blocos do Notion.
    """
    page_content = []

    if not thread_context:
        return None

    # 1. Resumo da IA
    if config.get('ai_summary_enabled'):
        messages = [msg async for msg in thread_context.history(limit=100)]
        if messages:
            summary_text = await summarize_thread_content(messages)
            if summary_text and not summary_text.startswith("Erro:"):
                page_content.append({
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"type": "text", "text": {"content": "🤖 Resumo da IA"}}]
                    }
                })

                # Usa a nova função do NotionIntegration para parsear o resumo
                parsed_summary_blocks = notion_integration._parse_summary_to_notion_blocks(summary_text) #
                page_content.extend(parsed_summary_blocks) # Adiciona os blocos processados

    # 2. Anexos (Imagens, GIFs, Vídeos)
    attachments = await get_thread_attachments(thread_context)
    if attachments:
        if page_content: # Adiciona um separador se já houver conteúdo
            page_content.append({
                "object": "block",
                "type": "divider",
                "divider": {}
            })
        page_content.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "📎 Anexos do Tópico"}}]
            }
        })
        for att in attachments:
            if att['type'] == 'image':
                page_content.append({
                    "object": "block",
                    "type": "image",
                    "image": {
                        "type": "external",
                        "external": {
                            "url": att['url']
                        }
                    }
                })
            elif att['type'] == 'video':
                # Para vídeos, o Notion geralmente requer embeds específicos ou o upload.
                # Como alternativa simples, adicionamos um link.
                page_content.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": f"Vídeo/GIF ({att['filename']}): "}}, {"type": "text", "text": {"content": att['url'], "link": {"url": att['url']}}}]
                    }
                })
            # Se quiser lidar com blocos 'embed' para YouTube, Vimeo, etc.,
            # seria necessário adicionar lógica para identificar a plataforma da URL.


    return page_content if page_content else None


async def start_editing_flow(interaction: Interaction, page_id_to_edit: str, config: dict, notion: NotionIntegration):
    """
    Inicia o fluxo completo de edição de um card do Notion,
    controlado por interações do Discord.
    """
    try:
        all_db_props = notion.get_properties_for_interaction(config['notion_url'])
        editable_props = [p for p in all_db_props if p['name'] in config.get('create_properties', [])]

        prop_msg = await interaction.followup.send("Iniciando edição...", ephemeral=True)

        while True:
            prop_select_view = View(timeout=180.0)
            prop_select = Select(placeholder="Escolha uma propriedade para editar...", options=[SelectOption(label=p['name'], description=f"Tipo: {p['type']}") for p in editable_props[:25]])
            prop_select_view.add_item(prop_select)

            await prop_msg.edit(content="Qual propriedade você quer alterar agora?", view=prop_select_view)

            prop_choice_interaction = None
            async def prop_select_callback(inter: Interaction):
                nonlocal prop_choice_interaction
                prop_choice_interaction = inter
                prop_select_view.stop()
            prop_select.callback = prop_select_callback

            await prop_select_view.wait()

            if prop_choice_interaction is None:
                await prop_msg.edit(content="⌛ Edição cancelada ou tempo esgotado.", view=None)
                break

            selected_prop_name = prop_select.values[0]
            selected_prop_details = next((p for p in editable_props if p['name'] == selected_prop_name), None)

            new_value = None
            prop_type = selected_prop_details['type']

            if prop_type in ['select', 'multi_select', 'status']:
                options_view = View(timeout=180.0)
                options_select = Select(
                    placeholder=f"Escolha para {selected_prop_name}",
                    options=[SelectOption(label=opt) for opt in selected_prop_details.get('options', [])[:25]],
                    max_values=len(selected_prop_details.get('options',[])) if prop_type == 'multi_select' else 1
                )

                options_view.result = None
                async def options_select_callback(inter_opt: Interaction):
                    await inter_opt.response.defer()
                    options_view.result = inter_opt.data['values']
                    options_view.stop()

                options_select.callback = options_select_callback
                options_view.add_item(options_select)

                await prop_choice_interaction.response.edit_message(content=f"Qual o novo valor para **{selected_prop_name}**?", view=options_view)
                await options_view.wait()

                if options_view.result:
                    new_value = options_view.result if prop_type == 'multi_select' else options_view.result[0]

            else:
                class EditModal(discord.ui.Modal, title=f"Editar '{selected_prop_name}'"):
                    new_val_input = discord.ui.TextInput(label="Novo valor", style=discord.TextStyle.paragraph)
                    async def on_submit(self, modal_inter: Interaction):
                        self.result = self.new_val_input.value
                        await modal_inter.response.defer()
                        self.stop()

                edit_modal = EditModal()
                await prop_choice_interaction.response.send_modal(edit_modal)
                await edit_modal.wait()
                new_value = getattr(edit_modal, 'result', None)

            if new_value is None:
                await prop_msg.edit(content="❌ Nenhum novo valor fornecido.", view=None)
                await asyncio.sleep(5)
                continue

            await prop_msg.edit(content=f"⚙️ Atualizando propriedade...", view=None)
            properties_payload = notion.build_update_payload(selected_prop_name, prop_type, new_value)
            notion.update_page(page_id_to_edit, properties_payload)

            continue_view = ContinueEditingView(interaction.user.id)
            await prop_msg.edit(content=f"✅ Propriedade **{selected_prop_name}** atualizada!\nDeseja continuar editando?", view=continue_view)
            await continue_view.wait()

            if continue_view.choice == 'finish':
                await prop_msg.edit(content="Finalizando...", view=None)
                break

        final_page_data = notion.get_page(page_id_to_edit)
        display_names = config.get('display_properties', [])
        final_embed = notion.format_page_for_embed(final_page_data, display_properties=display_names)

        if final_embed:
            publish_view = PublishView(interaction.user.id, final_embed, page_id_to_edit, config, notion)
            await prop_msg.edit(content="Edição concluída! Veja o resultado.", embed=final_embed, view=publish_view)
        else:
            await prop_msg.edit(content="✅ Edição concluída!", embed=None, view=None)

    except Exception as e:
        print(f"Erro no fluxo de edição: {e}")
        try:
            if 'prop_msg' in locals() and prop_msg:
                await prop_msg.edit(content=f"🔴 Um erro ocorreu durante a edição: {e}", view=None, embed=None)
            else:
                await interaction.followup.send(f"🔴 Um erro ocorreu durante a edição: {e}", ephemeral=True)
        except: pass


# --- CLASSES DE UI ---

class SelectView(View):
    def __init__(self, select_component: Select, author_id: int, timeout=180.0):
        super().__init__(timeout=timeout)
        self.select_component, self.author_id = select_component, author_id
        self.add_item(self.select_component)
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Você não pode interagir com o menu de outra pessoa.", ephemeral=True)
            return False
        return True

class CardActionView(View):
    def __init__(self, author_id: int, page_id: str, config: dict, notion: NotionIntegration):
        super().__init__(timeout=None)
        self.author_id, self.page_id, self.config = author_id, page_id, config
        self.notion = notion

    async def interaction_check(self, interaction: Interaction) -> bool:
        return True

    @discord.ui.button(label="✏️ Editar", style=ButtonStyle.secondary)
    async def edit_button(self, interaction: Interaction, button: Button):
        await interaction.response.send_message("Iniciando modo de edição para este card...", ephemeral=True)
        await start_editing_flow(interaction, self.page_id, self.config, self.notion)

    @discord.ui.button(label="🗑️ Excluir", style=ButtonStyle.danger)
    async def delete_button(self, interaction: Interaction, button: Button):
        confirm_view = View(timeout=60.0)
        yes_button = Button(label="Sim, excluir!", style=ButtonStyle.danger)
        no_button = Button(label="Cancelar", style=ButtonStyle.secondary)
        confirm_view.add_item(yes_button); confirm_view.add_item(no_button)

        async def yes_callback(inter: Interaction):
            confirm_view.stop()
            try:
                await inter.response.defer(ephemeral=True, thinking=True)
                self.notion.delete_page(self.page_id)

                for item in self.children: item.disabled = True

                original_embed = interaction.message.embeds[0]
                original_embed.title = f"[EXCLUÍDO] {original_embed.title}"
                original_embed.color = Color.dark_gray()
                original_embed.description = "Este card foi excluído."

                await interaction.message.edit(embed=original_embed, view=self)
                await inter.followup.send("✅ Card excluído com sucesso!", ephemeral=True)
            except Exception as e:
                await inter.followup.send(f"🔴 Erro ao excluir o card: {e}", ephemeral=True)

        async def no_callback(inter: Interaction):
            confirm_view.stop()
            await inter.response.edit_message(content="❌ Exclusão cancelada.", view=None)

        yes_button.callback, no_button.callback = yes_callback, no_callback
        await interaction.response.send_message("⚠️ **Você tem certeza que deseja excluir este card?**", view=confirm_view, ephemeral=True)


class PaginationView(View):
    def __init__(self, author: discord.Member, results: list, config: dict, notion: NotionIntegration, actions: List[str] = []):
        super().__init__(timeout=300.0)
        self.author, self.results, self.config, self.actions = author, results, config, actions
        self.notion = notion
        self.current_page, self.total_pages = 0, len(results)

        if 'edit' not in self.actions: self.remove_item(self.edit_button)
        if 'delete' not in self.actions: self.remove_item(self.delete_button)
        if 'share' not in self.actions: self.remove_item(self.share_button)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Você não pode interagir com os botões de outra pessoa.", ephemeral=True)
            return False
        return True

    def get_current_page_data(self):
        return self.results[self.current_page]

    async def get_page_embed(self) -> discord.Embed:
        page_data = self.get_current_page_data()
        embed = self.notion.format_page_for_embed(
            page_result=page_data,
            display_properties=self.config.get('display_properties', []),
            include_footer=True
        )
        embed.set_footer(text=f"Card {self.current_page + 1} de {self.total_pages}")
        return embed

    def update_nav_buttons(self):
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1

    @discord.ui.button(label="⬅️", style=ButtonStyle.secondary, row=0)
    async def previous_button(self, interaction: Interaction, button: Button):
        if self.current_page > 0: self.current_page -= 1
        self.update_nav_buttons()
        await interaction.response.edit_message(embed=await self.get_page_embed(), view=self)

    @discord.ui.button(label="➡️", style=ButtonStyle.secondary, row=0)
    async def next_button(self, interaction: Interaction, button: Button):
        if self.current_page < self.total_pages - 1: self.current_page += 1
        self.update_nav_buttons()
        await interaction.response.edit_message(embed=await self.get_page_embed(), view=self)

    @discord.ui.button(label="✏️ Editar", style=ButtonStyle.primary, row=1)
    async def edit_button(self, interaction: Interaction, button: Button):
        page_id = self.get_current_page_data()['id']
        await interaction.response.send_message(f"Iniciando modo de edição para este card...", ephemeral=True)
        await start_editing_flow(interaction, page_id, self.config, self.notion)

    @discord.ui.button(label="🗑️ Excluir", style=ButtonStyle.danger, row=1)
    async def delete_button(self, interaction: Interaction, button: Button):
        page_id = self.get_current_page_data()['id']

        confirm_view = View(timeout=60.0)
        yes_button = Button(label="Sim, excluir!", style=ButtonStyle.danger)
        no_button = Button(label="Cancelar", style=ButtonStyle.secondary)
        confirm_view.add_item(yes_button)
        confirm_view.add_item(no_button)

        async def yes_callback(inter: Interaction):
            await inter.response.defer(ephemeral=True, thinking=True)
            try:
                self.notion.delete_page(page_id)
                await interaction.edit_original_response(content="✅ Card excluído com sucesso.", view=None, embed=None)
                await inter.followup.send("Confirmado!", ephemeral=True)
            except Exception as e:
                await inter.followup.send(f"🔴 Erro ao excluir o card: {e}", ephemeral=True)

        async def no_callback(inter: Interaction):
            await inter.response.edit_message(content="❌ Exclusão cancelada.", view=None)

        yes_button.callback, no_button.callback = yes_callback, no_callback
        await interaction.response.send_message("⚠️ **Você tem certeza que deseja excluir este card?**", view=confirm_view, ephemeral=True)

    @discord.ui.button(label="📢 Exibir para Todos", style=ButtonStyle.success, row=2)
    async def share_button(self, interaction: Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        page_data = self.get_current_page_data()
        share_embed = self.notion.format_page_for_embed(
            page_result=page_data,
            display_properties=self.config.get('display_properties', [])
        )
        if share_embed:
            action_view = None
            if self.config.get('action_buttons_enabled', True):
                action_view = CardActionView(author_id=interaction.user.id, page_id=page_data['id'], config=self.config, notion=self.notion)

            await interaction.channel.send(f"{interaction.user.mention} compartilhou este card:", embed=share_embed, view=action_view)
            await interaction.followup.send("✅ Card exibido no canal!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Não foi possível gerar o embed para compartilhar.", ephemeral=True)


class SearchModal(discord.ui.Modal):
    def __init__(self, notion: NotionIntegration, config: dict, selected_property: dict):
        self.notion = notion
        self.config = config
        self.selected_property = selected_property
        super().__init__(title=f"Buscar por '{self.selected_property['name']}'")
        self.search_term_input = discord.ui.TextInput(label="Digite o termo que você quer procurar", style=discord.TextStyle.short, placeholder="Ex: 'Card de Teste'", required=True)
        self.add_item(self.search_term_input)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        search_term = self.search_term_input.value
        try:
            cards = self.notion.search_in_database(self.config['notion_url'], search_term, self.selected_property['name'], self.selected_property['type'])
            results = cards.get('results', [])
            if not results: return await interaction.followup.send(f"❌ Nenhum resultado encontrado para **'{search_term}'**.", ephemeral=True)

            await interaction.followup.send(f"✅ **{len(results)}** resultado(s) encontrado(s)! Veja abaixo:", ephemeral=True)

            view = PaginationView(interaction.user, results, self.config, self.notion, actions=['edit', 'delete', 'share'])
            view.update_nav_buttons()
            await interaction.followup.send(embed=await view.get_page_embed(), view=view, ephemeral=True)
        except NotionAPIError as e: await interaction.followup.send(f"❌ **Ocorreu um erro com o Notion:**\n`{e}`", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"🔴 **Ocorreu um erro inesperado:**\n`{e}`", ephemeral=True)
            print(f"Erro inesperado no on_submit do SearchModal: {e}")

class PublishView(View):
    def __init__(self, author_id: int, embed_to_publish: discord.Embed, page_id: str, config: dict, notion: NotionIntegration):
        super().__init__(timeout=300.0)
        self.author_id = author_id
        self.embed = embed_to_publish
        self.page_id = page_id
        self.config = config
        self.notion = notion

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Você não pode interagir com o menu de outra pessoa.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="📢 Exibir para Todos", style=ButtonStyle.primary)
    async def publish(self, interaction: Interaction, button: Button):
        button.disabled = True
        await interaction.response.edit_message(content="✅ Card publicado no canal!", view=self)

        action_view = None
        if self.config.get('action_buttons_enabled', True):
            action_view = CardActionView(author_id=self.author_id, page_id=self.page_id, config=self.config, notion=self.notion)

        await interaction.channel.send(embed=self.embed, view=action_view)
        self.stop()


class CardSelectPropertiesView(View):
    def __init__(self, author_id: int, config: dict, all_properties: list, select_props: list, collected_from_modal: dict, thread_context: Optional[discord.Thread], notion: NotionIntegration):
        super().__init__(timeout=300.0)
        self.author_id = author_id
        self.config = config
        self.all_properties = all_properties
        self.select_props = select_props
        self.collected_properties = collected_from_modal.copy()
        self.thread_context = thread_context
        self.notion = notion # A instância de NotionIntegration está disponível aqui.

        for prop in self.select_props:
            prop_name, prop_type = prop['name'], prop['type']
            options = [SelectOption(label=opt) for opt in prop.get('options', [])[:25]]
            is_multi = prop_type == 'multi_select'
            placeholder = "Escolha uma ou mais opções..." if is_multi else "Escolha uma opção..."
            select_menu = Select(placeholder=f"{placeholder} para {prop_name}", options=options, max_values=len(options) if is_multi else 1, min_values=0, custom_id=f"select_{prop_name}")
            select_menu.callback = self.on_select_callback
            self.add_item(select_menu)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Você não pode interagir com o menu de outra pessoa.", ephemeral=True)
            return False
        return True

    async def on_select_callback(self, interaction: Interaction):
        select_menu_data = interaction.data
        prop_name = select_menu_data['custom_id'].replace("select_", "")
        values = select_menu_data.get('values', [])
        if len(values) > 1: self.collected_properties[prop_name] = values
        elif values: self.collected_properties[prop_name] = values[0]
        await interaction.response.defer()

    @discord.ui.button(label="✅ Criar Card", style=ButtonStyle.green, row=4)
    async def confirm_button(self, interaction: Interaction, button: Button):
        for item in self.children: item.disabled = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            title_prop_name = next((p['name'] for p in self.all_properties if p['type'] == 'title'), None)
            if not title_prop_name: raise NotionAPIError("Nenhuma propriedade de Título foi encontrada.")

            title_value = self.collected_properties.pop(title_prop_name, f"Card criado em {datetime.now().strftime('%d/%m')}")

            individual_prop = self.config.get('individual_person_prop')
            if individual_prop:
                self.collected_properties[individual_prop] = interaction.user.display_name

            collective_prop = self.config.get('collective_person_prop')
            if collective_prop and self.thread_context:
                participants = await get_topic_participants(self.thread_context)
                notion_user_ids = [self.notion.search_id_person(member.display_name) for member in participants]
                self.collected_properties[collective_prop] = [uid for uid in notion_user_ids if uid]

            topic_prop_name = self.config.get('topic_link_property_name')
            if topic_prop_name and self.thread_context:
                self.collected_properties[topic_prop_name] = self.thread_context.jump_url

            # CORREÇÃO APLICADA AQUI: Passando self.notion como argumento
            page_content = await _build_notion_page_content(self.config, self.thread_context, self.notion) #

            # Modifica a chamada para a criação da página
            page_properties = self.notion.build_page_properties(self.config['notion_url'], title_value, self.collected_properties)
            response = self.notion.insert_into_database(
                self.config['notion_url'],
                page_properties,
                children=page_content
            )

            await interaction.edit_original_response(content="✅ Card criado com sucesso! Veja abaixo.", view=None)

            display_properties_names = self.config.get('display_properties', [])
            success_embed = self.notion.format_page_for_embed(response, display_properties=display_properties_names)

            if not success_embed:
                await interaction.followup.send("❌ Não foi possível formatar o embed do card criado.", ephemeral=True)
                return

            success_embed.title = f"✅ Card '{success_embed.title.replace('📌 ', '')}' Criado!"
            success_embed.color = Color.purple()

            page_id = response['id']
            publish_view = PublishView(interaction.user.id, success_embed, page_id, self.config, self.notion)
            await interaction.followup.send(
                "Use o botão abaixo para exibir seu card para todos no canal.",
                embed=success_embed, view=publish_view, ephemeral=True
            )

        except NotionAPIError as e: await interaction.followup.send(f"❌ **Erro no Notion:**\n`{e}`", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"🔴 **Erro inesperado:**\n`{e}`", ephemeral=True)
            print(f"Erro inesperado no confirm_button: {e}")


class CardModal(discord.ui.Modal):
    def __init__(self, notion: NotionIntegration, config: dict, all_properties: list, text_props: list, select_props: list, thread_context: Optional[discord.Thread], topic_title: Optional[str]):
        super().__init__(title="Criar Novo Card (Etapa 1)")
        self.notion = notion # A instância de NotionIntegration está disponível aqui.
        self.config, self.all_properties, self.text_props, self.select_props = config, all_properties, text_props, select_props
        self.thread_context = thread_context
        self.text_inputs = {}

        for prop in self.text_props:
            prop_name, prop_type = prop['name'], prop['type']
            text_style = discord.TextStyle.paragraph if any(k in prop_name.lower() for k in ["desc", "detalhe"]) else discord.TextStyle.short
            default_value = topic_title if prop_type == 'title' else None
            text_input = discord.ui.TextInput(label=prop_name, style=text_style, required=(prop_type == 'title'), default=default_value)
            self.text_inputs[prop_name] = text_input
            self.add_item(text_input)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        collected_from_modal = {name: item.value for name, item in self.text_inputs.items() if item.value}

        if not self.select_props:
            try:
                title_prop_name = next((p['name'] for p in self.all_properties if p['type'] == 'title'), None)
                if not title_prop_name: raise NotionAPIError("Propriedade de Título não encontrada.")
                title_value = collected_from_modal.pop(title_prop_name, f"Card criado em {datetime.now().strftime('%d/%m')}")

                individual_prop = self.config.get('individual_person_prop')
                if individual_prop:
                    collected_from_modal[individual_prop] = interaction.user.display_name

                collective_prop = self.config.get('collective_person_prop')
                if collective_prop and self.thread_context:
                    participants = await get_topic_participants(self.thread_context)
                    notion_user_ids = [self.notion.search_id_person(member.display_name) for member in participants]
                    collected_from_modal[collective_prop] = [uid for uid in notion_user_ids if uid]

                topic_prop_name = self.config.get('topic_link_property_name')
                if topic_prop_name and self.thread_context:
                    collected_from_modal[topic_prop_name] = self.thread_context.jump_url

                # CORREÇÃO APLICADA AQUI: Passando self.notion como argumento
                page_content = await _build_notion_page_content(self.config, self.thread_context, self.notion) #

                # Modifica a chamada para a criação da página
                page_properties = self.notion.build_page_properties(self.config['notion_url'], title_value, collected_from_modal)
                response = self.notion.insert_into_database(
                    self.config['notion_url'],
                    page_properties,
                    children=page_content
                )

                display_names = self.config.get('display_properties', [])
                final_embed = self.notion.format_page_for_embed(response, display_properties=display_names)

                if final_embed:
                    final_embed.title = f"✅ Card '{final_embed.title.replace('📌 ', '')}' Criado!"
                    final_embed.color = Color.purple()
                    page_id = response['id']
                    publish_view = PublishView(interaction.user.id, final_embed, page_id, self.config, self.notion)
                    await interaction.followup.send("Card criado! Use o botão abaixo para exibi-lo para todos.", embed=final_embed, view=publish_view, ephemeral=True)
                else:
                    await interaction.followup.send("✅ Card criado, mas não foi possível gerar a visualização.", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"🔴 Erro ao criar o card: {e}", ephemeral=True)
        else:
            await interaction.edit_original_response(content="📝 Etapa 1/2 concluída. Agora, selecione os valores abaixo.", view=None)
            view = CardSelectPropertiesView(interaction.user.id, self.config, self.all_properties, self.select_props, collected_from_modal, self.thread_context, self.notion)
            await interaction.followup.send(view=view, ephemeral=True)


class ContinueEditingView(View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180.0)
        self.author_id = author_id
        self.choice = None
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Você não pode interagir com o menu de outra pessoa.", ephemeral=True)
            return False
        return True
    @discord.ui.button(label="✏️ Editar outra propriedade", style=ButtonStyle.secondary)
    async def continue_editing(self, interaction: Interaction, button: Button):
        self.choice = 'continue'
        await interaction.response.edit_message(content="Continuando edição...", view=None)
        self.stop()
    @discord.ui.button(label="✅ Concluir Edição", style=ButtonStyle.success)
    async def finish_editing(self, interaction: Interaction, button: Button):
        self.choice = 'finish'
        await interaction.response.edit_message(content="Finalizando...", view=None)
        self.stop()


class PersonSelectView(View):
    def __init__(self, guild_id: int, channel_id: int, compatible_props: list, config_key: str):
        super().__init__(timeout=180.0)

        options = [SelectOption(label=prop['name'], description=f"Tipo: {prop['type']}") for prop in compatible_props[:25]]
        prop_select = Select(placeholder="Selecione a propriedade de Pessoa...", options=options)

        async def select_callback(interaction: Interaction):
            selected_prop_name = interaction.data['values'][0]
            save_config(guild_id, channel_id, {config_key: selected_prop_name})
            await interaction.response.edit_message(content=f"✅ Configuração salva! A propriedade **'{selected_prop_name}'** será usada.", view=None)

        prop_select.callback = select_callback
        self.add_item(prop_select)


class TopicLinkView(View):
    def __init__(self, guild_id: int, channel_id: int, compatible_props: list):
        super().__init__(timeout=180.0)

        options = [SelectOption(label=prop['name'], description=f"Tipo: {prop['type']}") for prop in compatible_props[:25]]
        prop_select = Select(placeholder="Selecione a propriedade para salvar o link...", options=options)

        async def select_callback(interaction: Interaction):
            selected_prop_name = interaction.data['values'][0]
            save_config(guild_id, channel_id, {'topic_link_property_name': selected_prop_name})
            await interaction.response.edit_message(content=f"✅ O link do tópico será salvo na propriedade **'{selected_prop_name}'**.", view=None)

        prop_select.callback = select_callback
        self.add_item(prop_select)


class ManagementView(View):
    def __init__(self, parent_interaction: Interaction, notion: NotionIntegration, config: dict):
        super().__init__(timeout=180.0)
        self.parent_interaction = parent_interaction
        self.guild_id = parent_interaction.guild_id
        self.channel_id = parent_interaction.channel.parent_id if isinstance(parent_interaction.channel, discord.Thread) else parent_interaction.channel.id
        self.notion = notion
        self.config = config

    @discord.ui.button(label="Reconfigurar URL", style=ButtonStyle.primary, emoji="🔄", row=0)
    async def reconfigure(self, interaction: Interaction, button: Button):
        await interaction.response.send_message("Para reconfigurar, use `/config` novamente com a nova URL do Notion.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Gerenciar Botões de Ação", style=ButtonStyle.secondary, emoji="⚙️", row=0)
    async def manage_buttons(self, interaction: Interaction, button: Button):
        is_enabled = self.config.get('action_buttons_enabled', True)
        toggle_view = View(timeout=60.0)
        button_label = "Desativar Botões (Editar/Excluir)" if is_enabled else "Ativar Botões (Editar/Excluir)"
        button_style = ButtonStyle.danger if is_enabled else ButtonStyle.success
        toggle_button = Button(label=button_label, style=button_style)

        async def toggle_callback(inter: Interaction):
            new_state = not is_enabled
            save_config(self.guild_id, self.channel_id, {'action_buttons_enabled': new_state})
            status_text = "ATIVADOS" if new_state else "DESATIVADOS"
            await inter.response.edit_message(content=f"✅ Botões de ação foram **{status_text}**.", view=None)

        toggle_button.callback = toggle_callback
        toggle_view.add_item(toggle_button)
        await interaction.response.send_message(f"Botões de ação estão **{'ATIVADOS' if is_enabled else 'DESATIVADOS'}**.", view=toggle_view, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Resumir com IA", style=ButtonStyle.secondary, emoji="✨", row=1)
    async def manage_ai_summary(self, interaction: Interaction, button: Button):
        is_enabled = self.config.get('ai_summary_enabled', False) # Padrão é desativado
        toggle_view = View(timeout=60.0)
        button_label = "Desativar Resumo por IA" if is_enabled else "Ativar Resumo por IA"
        button_style = ButtonStyle.danger if is_enabled else ButtonStyle.success
        toggle_button = Button(label=button_label, style=button_style)

        async def toggle_callback(inter: Interaction):
            new_state = not is_enabled
            save_config(self.guild_id, self.channel_id, {'ai_summary_enabled': new_state})
            status_text = "ATIVADO" if new_state else "DESATIVADO"
            await inter.response.edit_message(content=f"✅ Resumo de tópico por IA foi **{status_text}**.", view=None)

        toggle_button.callback = toggle_callback
        toggle_view.add_item(toggle_button)

        status_atual = "ATIVADO" if is_enabled else "DESATIVADO"
        msg = f"O resumo automático de tópicos por IA está **{status_atual}**.\n\nQuando ativado, ao criar um card dentro de um tópico, a IA irá ler a conversa, gerar um resumo e adicioná-lo ao corpo do card no Notion."
        await interaction.response.send_message(msg, view=toggle_view, ephemeral=True)
        self.stop()


    @discord.ui.button(label="Configurar Link de Tópico", style=ButtonStyle.secondary, emoji="🔗", row=2)
    async def configure_topic_link(self, interaction: Interaction, button: Button):
        all_props = self.notion.get_properties_for_interaction(self.config['notion_url'])
        compatible_props = [p for p in all_props if p['type'] in ['rich_text', 'url']]
        if not compatible_props:
            return await interaction.response.send_message("❌ Nenhuma propriedade compatível (Texto/URL) encontrada.", ephemeral=True)

        current_prop = self.config.get('topic_link_property_name')
        description = f"**Propriedade Atual:** `{current_prop}`\n\nSelecione uma nova propriedade para salvar o link." if current_prop else "Selecione uma propriedade para salvar o link do tópico."

        view = TopicLinkView(self.guild_id, self.channel_id, compatible_props)
        await interaction.response.send_message(description, view=view, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Definir Dono do Card", style=ButtonStyle.secondary, emoji="👤", row=3)
    async def configure_individual_person(self, interaction: Interaction, button: Button):
        all_props = self.notion.get_properties_for_interaction(self.config['notion_url'])
        people_props = [p for p in all_props if p['type'] == 'people']
        if not people_props:
            return await interaction.response.send_message("❌ Nenhuma propriedade 'Pessoa' encontrada.", ephemeral=True)

        current_prop = self.config.get('individual_person_prop')
        description = f"**Propriedade Atual:** `{current_prop}`\n\nSelecione a propriedade para preencher com o autor do comando `/card`."

        view = PersonSelectView(self.guild_id, self.channel_id, people_props, 'individual_person_prop')
        await interaction.response.send_message(description, view=view, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Definir Envolvidos do Tópico", style=ButtonStyle.secondary, emoji="👥", row=3)
    async def configure_collective_person(self, interaction: Interaction, button: Button):
        all_props = self.notion.get_properties_for_interaction(self.config['notion_url'])
        people_props = [p for p in all_props if p['type'] == 'people']
        if not people_props:
            return await interaction.response.send_message("❌ Nenhuma propriedade 'Pessoa' encontrada.", ephemeral=True)

        current_prop = self.config.get('collective_person_prop')
        description = f"**Propriedade Atual:** `{current_prop}`\n\nSelecione a propriedade para preencher com os participantes do tópico. (Deve suportar múltiplas pessoas)"

        view = PersonSelectView(self.guild_id, self.channel_id, people_props, 'collective_person_prop')
        await interaction.response.send_message(description, view=view, ephemeral=True)
        self.stop()