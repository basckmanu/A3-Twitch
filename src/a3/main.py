# src/a3/main.py
# Point d'entrée global du projet A3.

from a3.Twitch.mainTwitch import TwitchBot, setup_logging

if __name__ == "__main__":
    logger = setup_logging()
    bot = TwitchBot(logger)
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("\nArrêt manuel demandé (Ctrl+C). Fermeture en cours...")
