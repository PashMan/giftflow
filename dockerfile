FROM python:3.10

WORKDIR /app

# Устанавливаем базу, wget уже не так критичен, но пусть будет
RUN apt-get update && apt-get install -y wget && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код И наш файл root.crt, который мы загрузили в гит
COPY . .

# Создаем папку для сертификата
RUN mkdir -p /root/.postgresql

# Просто перемещаем файл сертификата в нужную папку
# (Мы больше не качаем его через wget!)
RUN cp root.crt /root/.postgresql/root.crt

CMD ["python", "main.py"]
