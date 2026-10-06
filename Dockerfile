FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Poster shriftlari (Google Fonts, OFL litsenziya)
RUN mkdir -p fonts && cd fonts \
 && curl -fsSL -o Unbounded.ttf "https://github.com/google/fonts/raw/main/ofl/unbounded/Unbounded%5Bwght%5D.ttf" \
 && curl -fsSL -o Manrope.ttf "https://github.com/google/fonts/raw/main/ofl/manrope/Manrope%5Bwght%5D.ttf" \
 && curl -fsSL -o BarlowCondensed-Bold.ttf "https://github.com/google/fonts/raw/main/ofl/barlowcondensed/BarlowCondensed-Bold.ttf" \
 || echo "Shriftlar yuklanmadi — DejaVu ishlatiladi"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV TZ_NAME=Asia/Tashkent PYTHONUNBUFFERED=1
CMD ["python", "main.py"]
