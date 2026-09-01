# For later 24/7 hosting (Railway, Fly.io, any VPS).
# The index ships with the image; the PDF and extraction scripts are not needed
# at runtime.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir discord.py python-dotenv rapidfuzz

COPY bot/ bot/
COPY data/rules.json data/rules.json

# Supply DISCORD_TOKEN as an environment variable on the host - do not bake it in.
CMD ["python", "-m", "bot.main"]
