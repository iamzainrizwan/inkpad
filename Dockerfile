FROM python:3.12-slim
# LibreOffice (pptx/odp/key -> pdf) + poppler (pdf -> png per slide).
# Carlito/Caladea are metric clones of Calibri/Cambria, so decks keep their line breaks.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-impress poppler-utils \
      fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY server.py index.html ./
ENV INKPAD_DATA=/data/notebook.json PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
CMD ["python3", "server.py"]
