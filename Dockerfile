# Используем официальный образ Python
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

RUN pip freeze > /app/requirements.txt

# Копируем requirements.txt
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы проекта
COPY . .

# Создаем директорию для данных, если её нет
RUN mkdir -p dataprice

# Открываем порт Streamlit (по умолчанию 8501)
EXPOSE 8501

# Настройки Streamlit для production
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Запускаем Streamlit приложение
CMD ["streamlit", "run", "app.py"]
