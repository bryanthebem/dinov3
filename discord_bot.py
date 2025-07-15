import discord
from discord.ext import commands
from ia_processor import IAProcessor
from config import DISCORD_TOKEN

class DiscordBot:
    def __init__(self):
        self.ia = IAProcessor()

        intents = discord.Intents.default()
        intents.message_content = True  # Habilita o conteúdo da mensagem
        self.bot = commands.Bot(command_prefix="!", intents=intents)

        @self.bot.event
        async def on_ready():
            print(f'✅ Bot conectado como {self.bot.user}')

        @self.bot.event
        async def on_message(message):
            if message.author == self.bot.user:
                return
            
            # Verifica se o bot foi mencionado
            if self.bot.user.mention in message.content:
                await message.channel.send("Olá! Eu sou um bot 🤖")

            await self.bot.process_commands(message)  # Processa comandos normalmente

        # Registra os comandos
        @self.bot.command()
        async def oi(ctx):
            await ctx.send("Olá! Eu sou um bot 🤖")

        @self.bot.command()
        async def pergunta(ctx, *, texto):
            resposta = self.ia.gerar_resposta(texto)
            await ctx.send(resposta)

    def run(self):
        self.bot.run(DISCORD_TOKEN)
