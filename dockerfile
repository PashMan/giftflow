FROM python:3.10-slim

WORKDIR /app

# Ставим wget для скачивания сертификата
RUN apt-get update && apt-get install -y wget && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Скачиваем сертификат для CockroachDB (в папку root, т.к. на Render мы root)
RUN mkdir -p /root/.postgresql && \
    wget "https://cockroachlabs.cloud/clusters/root.crt" -O /root/.postgresql/root.crt

CMD ["python", "main.py"]

def main():
    app = web.Application()
    app.router.add_route('OPTIONS', '/api/{tail:.*}', handle_options)
    app.router.add_post('/api/chats', api_get_chats)
    app.router.add_post('/api/collections/create', api_create)
    app.router.add_post('/api/collections/my', api_my)
    app.router.add_post('/api/collections/info', api_info)
    app.router.add_post('/api/collections/update', api_update)
    app.router.add_post('/api/collections/delete', api_delete)
    app.router.add_post('/api/collections/invoice', api_invoice)
    app.router.add_post('/api/upload', handle_upload)
    app.router.add_get('/', serve_index)
    app.router.add_get('/script.js', serve_script)
    app.router.add_get('/style.css', serve_style)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, port=WEB_SERVER_PORT)

if __name__ == "__main__":
    main()
