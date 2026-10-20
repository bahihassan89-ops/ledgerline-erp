FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ERP_ENV=prod
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --uid 10001 --no-create-home erp && python manage.py collectstatic --noinput 2>/dev/null || true
USER erp
EXPOSE 8000
CMD ["gunicorn", "erp.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60", "--access-logfile", "-"]
