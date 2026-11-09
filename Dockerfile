FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV POETRY_VERSION=2.5.1

WORKDIR /app

# apt-get upgrade: security fixes not yet in the base image
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Up-to-date pip (the base image's pip vendors outdated urllib3/msgpack)
RUN pip install --no-cache-dir --upgrade pip

# Installation de Poetry
RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"

COPY pyproject.toml poetry.lock ./

RUN poetry config virtualenvs.create false && \
    poetry install --only main --no-interaction --no-root

COPY . .

EXPOSE 9000

CMD ["python", "main.py"]