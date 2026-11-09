FROM python:3.12-slim
WORKDIR /app
COPY demo/vercel/index.html demo/vercel/favicon.svg demo/vercel/kinu-logo.svg ./
COPY demo/vercel/api/status.py ./status.py
COPY demo/railway/server.py ./server.py
ENV PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
