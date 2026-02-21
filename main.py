#C'est le point d'entrée. Il lance le bot, connecte la base de données et initialise le watcher.

from twitchio.ext import commands
from config import TOKEN, CHANNEL

class ChatWatcher(commands.Bot):

    def __init__(self):
        super().__init__(token=TOKEN, prefix='?', initial_channels=[CHANNEL])

    async def event_ready(self):
        print(f'👀 WATCHER ACTIVÉ : Connecté au chat de {CHANNEL}')
        print('-' * 40)

    async def event_message(self, message):
        if message.echo:
            return
        print(f"[{message.author.name}]: {message.content}")

if __name__ == "__main__":
    bot = ChatWatcher()
    bot.run()