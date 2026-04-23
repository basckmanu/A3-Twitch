# src/a3/main.py
# Point d'entrée global du projet A3.

from a3.Twitch.mainTwitch import TwitchBot

if __name__ == "__main__":
    bot = TwitchBot()
    bot.run()
