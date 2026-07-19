FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chown -R app:app /app
USER app

EXPOSE 5000
CMD ["sh", "-c", "flask --app app db upgrade && gunicorn --bind 0.0.0.0:5000 --worker-class gthread --workers ${GUNICORN_WORKERS:-2} --threads ${GUNICORN_THREADS:-8} --timeout 120 --graceful-timeout 30 --keep-alive 5 --access-logfile - 'app:create_app()'"]
