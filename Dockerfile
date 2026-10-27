# The site and console in sample mode: recorded service responses, no keys on the server.
FROM python:3.12-slim
RUN useradd -m -u 1000 portorium
WORKDIR /app
COPY --chown=portorium . .
RUN pip install --no-cache-dir -e . && chown portorium /app
USER portorium
ENV SAMPLE_MODE=true PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn portorium.web:app --host 0.0.0.0 --port ${PORT}"]
