FROM python:3.12.13-slim-bookworm
WORKDIR /app
COPY server/connectors/resend/check.py connectors/resend/check.py
COPY examples/render/app.py app.py
USER 65532:65532
EXPOSE 10000
CMD ["python", "app.py"]
