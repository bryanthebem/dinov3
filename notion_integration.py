# notion_integration.py (Versão com correção da busca por 'people' e formatação de IA com parser Markdown)

from notion_client import Client
import os
from dotenv import load_dotenv
import re
from datetime import datetime
from typing import List, Optional, Dict, Any
import discord

load_dotenv()

class NotionAPIError(Exception):
    """Exceção customizada para erros da API do Notion."""
    pass

class NotionIntegration:
    def __init__(self):
        self.token = os.getenv("NOTION_TOKEN")
        if not self.token:
            raise ValueError("O token do Notion (NOTION_TOKEN) não foi encontrado no seu ambiente.")
        self.notion = Client(auth=self.token)

    def _format_property_value(self, prop_type: str, prop_value):
        """Função auxiliar para formatar um valor para a API do Notion."""
        if prop_type == 'title': return {"title": [{"text": {"content": str(prop_value)}}]}
        elif prop_type == 'rich_text': return {"rich_text": [{"text": {"content": str(prop_value)}}]}
        elif prop_type == 'url': return {"url": prop_value}
        elif prop_type == 'status': return {"status": {"name": str(prop_value)}}
        elif prop_type == 'select':
            value = prop_value[0] if isinstance(prop_value, list) else prop_value
            return {"select": {"name": str(value)}}
        elif prop_type == 'multi_select':
            tags_to_add = prop_value if isinstance(prop_value, list) else [tag.strip() for tag in str(prop_value).split(',') if tag.strip()]
            return {"multi_select": [{"name": tag} for tag in tags_to_add]}
        elif prop_type == 'date':
            if not prop_value or not isinstance(prop_value, str): return None
            date_formats = ["%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d"]
            date_obj = None
            for fmt in date_formats:
                try:
                    date_obj = datetime.strptime(prop_value, fmt)
                    break
                except (ValueError, TypeError): continue
            if date_obj: return {"date": {"start": date_obj.strftime('%Y-%m-%d')}}
            else:
                print(f"Aviso: Não foi possível interpretar a data '{prop_value}'.")
                return None
        elif prop_type == 'people':
            if isinstance(prop_value, list):
                return {"people": [{"id": user_id} for user_id in prop_value]}
            try:
                user_id = self.search_id_person(str(prop_value))
                if user_id: return {"people": [{"id": user_id}]}
            except NotionAPIError as e: print(f"Aviso: {e}. Propriedade 'people' será ignorada.")
        return None

    def _convert_text_to_notion_rich_text_objects(self, text_content: str):
        """
        Converte uma string de texto para uma lista de objetos Rich Text do Notion,
        interpretando **negrito** e _itálico_.
        """
        rich_text_objects = []
        # Expressão regular para encontrar negritos e itálicos
        # Captura o texto entre ** ou _
        parts = re.split(r'(\*\*.*?\*\*|_.*?_)', text_content)

        for part in parts:
            if not part:
                continue

            annotations = {"bold": False, "italic": False}
            clean_text = part

            if part.startswith('**') and part.endswith('**') and len(part) >= 4:
                annotations["bold"] = True
                clean_text = part[2:-2]
            elif part.startswith('_') and part.endswith('_') and len(part) >= 2:
                annotations["italic"] = True
                clean_text = part[1:-1]
            
            rich_text_objects.append({
                "type": "text",
                "text": {"content": clean_text},
                "annotations": annotations
            })
        return rich_text_objects
    
    def _parse_summary_to_notion_blocks(self, summary_text: str) -> List[Dict]:
        """
        Parses o texto do resumo da IA (que pode conter Markdown) em blocos do Notion.
        Trata títulos em negrito, itens de lista e parágrafos.
        """
        notion_blocks = []
        lines = summary_text.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # CORREÇÃO APLICADA: Trata cabeçalhos em negrito (ex: **Título:**)
            bold_heading_match = re.match(r'^\*\*(.*?):\*\*$', line.strip())
            if bold_heading_match:
                heading_text = bold_heading_match.group(1) + ":"
                # Usa um bloco de cabeçalho para semântica e robustez
                notion_blocks.append({
                    "object": "block",
                    "type": "heading_3", # Usar um heading é mais apropriado
                    "heading_3": {
                        "rich_text": [{"type": "text", "text": {"content": heading_text}}]
                    }
                })
            # Trata itens de lista
            elif line.startswith('* ') or line.startswith('- '):
                content_text = line[2:]
                notion_blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": self._convert_text_to_notion_rich_text_objects(content_text)
                    }
                })
            # Trata parágrafos normais
            else:
                notion_blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": self._convert_text_to_notion_rich_text_objects(line)
                    }
                })
        return notion_blocks

    def extract_database_id(self, url):
        match = re.search(r"([a-f0-9]{32})", url)
        if match: return match.group(1)
        return None

    def search_in_database(self, url, search_term, filter_property, property_type="rich_text"):
        database_id = self.extract_database_id(url)
        if not database_id: raise NotionAPIError("ID da base de dados não encontrado na URL.")
        filter_criteria = {"property": filter_property}

        if property_type in ["rich_text", "title"]:
            filter_criteria[property_type] = {"contains": search_term}
        elif property_type in ["status", "select"]:
            filter_criteria[property_type] = {"equals": search_term}
        elif property_type == "people":
            pessoa_id = self.search_id_person(search_term)
            if pessoa_id:
                filter_criteria["people"] = {"contains": pessoa_id}
            else:
                return {"results": []} # Se não encontrar a pessoa, retorna uma busca vazia para não dar erro
        try:
            return self.notion.databases.query(database_id=database_id, filter=filter_criteria)
        except Exception as e:
            raise NotionAPIError(f"Erro ao buscar no Notion: {e}")

    def get_database_properties(self, url):
        database_id = self.extract_database_id(url)
        if not database_id: raise NotionAPIError("ID da base de dados não encontrado na URL.")
        try:
            return self.notion.databases.retrieve(database_id)['properties']
        except Exception as e: raise NotionAPIError(f"Erro ao obter propriedades do Notion: {e}")

    def search_id_person(self, search_term: str):
        if not isinstance(search_term, str) or not search_term:
            return None
        try:
            users = self.notion.users.list()
            search_term_lower = search_term.lower()
            for user in users.get("results", []):
                user_name = user.get("name")
                if user_name and search_term_lower in user_name.lower():
                    return user.get("id")
                user_email = user.get("person", {}).get("email")
                if user_email and user_email.lower() == search_term_lower:
                    return user.get("id")
            return None
        except Exception as e:
            print(f"Erro ao buscar usuários do Notion: {e}")
            raise NotionAPIError(f"Não foi possível buscar os usuários no Notion.")

    def get_database_count(self, url):
        database_id = self.extract_database_id(url)
        if not database_id: raise NotionAPIError("ID da base de dados não encontrado na URL.")
        try:
            query_result = self.notion.databases.query(database_id)
            return len(query_result['results'])
        except Exception as e: raise NotionAPIError(f"Erro ao contar páginas no Notion: {e}")

    def insert_into_database(self, url, properties, children: Optional[List[Dict]] = None):
        """
        Cria uma nova página no Notion, com propriedades e, opcionalmente, conteúdo (children).
        """
        database_id = self.extract_database_id(url)
        if not database_id:
            raise NotionAPIError("ID da base de dados não encontrado na URL.")

        payload = {
            "parent": {"database_id": database_id},
            "properties": properties
        }
        if children:
            payload["children"] = children

        try:
            return self.notion.pages.create(**payload)
        except Exception as e:
            raise NotionAPIError(f"Erro ao criar a página no Notion: {e}")

    def build_page_properties(self, db_url: str, title: str, properties_dict: dict):
        schema = self.get_database_properties(db_url)
        page_properties = {}
        title_prop_name = next((name for name, data in schema.items() if data['type'] == 'title'), None)
        if title_prop_name:
            page_properties[title_prop_name] = self._format_property_value('title', title)

        for prop_name, prop_value in properties_dict.items():
            prop_data = schema.get(prop_name)
            if not prop_data:
                print(f"AVISO: A propriedade '{prop_name}' não foi encontrada na base de dados. Ela será ignorada.")
                continue
            formatted_prop = self._format_property_value(prop_data.get('type'), prop_value)
            if formatted_prop:
                page_properties[prop_name] = formatted_prop
        return page_properties

    def build_update_payload(self, prop_name: str, prop_type: str, prop_value):
        formatted_prop = self._format_property_value(prop_type, prop_value)
        if formatted_prop:
            return {prop_name: formatted_prop}
        return {}

    def extract_value_from_property(self, prop_data, prop_type):
        try:
            if prop_type == 'title': return prop_data.get('title', [{}])[0].get('plain_text', '')
            elif prop_type == 'rich_text': return "".join([part.get('plain_text', '') for part in prop_data.get('rich_text', [])])
            elif prop_type == 'status': return prop_data.get('status', {}).get('name', '')
            elif prop_type == 'select': return prop_data.get('select', {}).get('name', '')
            elif prop_type == 'multi_select': return ", ".join([tag.get('name', '') for tag in prop_data.get('multi_select', [])])
            elif prop_type == 'people': return ", ".join([person.get('name', 'Usuário Desconhecido') for person in prop_data.get('people', [])])
            elif prop_type == 'date':
                date_info = prop_data.get('date')
                if date_info and date_info.get('start'):
                    return datetime.fromisoformat(date_info['start']).strftime('%d/%m/%Y')
                return ''
            elif prop_type == 'url': return prop_data.get('url', '')
            elif prop_type == 'number': return str(prop_data.get('number', ''))
            return ''
        except (IndexError, TypeError, AttributeError):
            return ''


    def get_properties_for_interaction(self, url):
        all_props = self.get_database_properties(url)
        properties_to_ask, title_prop = [], None
        excluded_types = ['rollup', 'created_by', 'created_time', 'last_edited_by', 'last_edited_time', 'formula']
        for prop_name, prop_data in all_props.items():
            prop_type = prop_data.get('type')
            if prop_type in excluded_types: continue
            prop_info = {'name': prop_name, 'type': prop_type, 'options': None}
            if prop_type == 'select': prop_info['options'] = [opt['name'] for opt in prop_data.get('select', {}).get('options', [])]
            elif prop_type == 'multi_select': prop_info['options'] = [opt['name'] for opt in prop_data.get('multi_select', {}).get('options', [])]
            elif prop_type == 'status': prop_info['options'] = [opt['name'] for opt in prop_data.get('status', {}).get('options', [])]

            if prop_type == 'title':
                title_prop = prop_info
            else:
                properties_to_ask.append(prop_info)

        if title_prop:
            properties_to_ask.insert(0, title_prop)
        return properties_to_ask

    def format_page_for_embed(self, page_result: dict, display_properties: Optional[List[str]] = None, include_footer: bool = False) -> Optional[discord.Embed]:
        if not page_result: return None
        properties = page_result.get('properties', {})
        page_url, title = page_result.get('url', '#'), "Card sem título"
        fields = []
        props_to_iterate = display_properties if display_properties is not None else list(properties.keys())

        for prop_name in props_to_iterate:
            prop_data = properties.get(prop_name)
            if not prop_data: continue
            prop_type = prop_data.get('type')
            value = self.extract_value_from_property(prop_data, prop_type)
            if prop_type == 'title':
                title = value if value else title
                continue
            if value:
                fields.append({'name': prop_name, 'value': str(value)})

        embed = discord.Embed(title=f"📌 {title}", url=page_url, color=discord.Color.green())
        for field in fields:
            embed.add_field(name=field['name'], value=field['value'], inline=False)
        if include_footer:
            embed.set_footer(text="Resultado da busca")

        return embed

    def update_page(self, page_id: str, properties: dict):
        try:
            return self.notion.pages.update(page_id=page_id, properties=properties)
        except Exception as e: raise NotionAPIError(f"Erro ao atualizar a página no Notion: {e}")

    def get_page(self, page_id: str):
        try:
            return self.notion.pages.retrieve(page_id=page_id)
        except Exception as e: raise NotionAPIError(f"Erro ao buscar a página no Notion: {e}")

    def delete_page(self, page_id: str):
        """Arquiva (deleta) uma página no Notion."""
        try:
            return self.notion.pages.update(page_id=page_id, archived=True)
        except Exception as e:
            raise NotionAPIError(f"Erro ao deletar (arquivar) a página no Notion: {e}")
