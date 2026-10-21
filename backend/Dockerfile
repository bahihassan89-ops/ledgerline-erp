FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ERP_ENV=prod
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --uid 10001 --no-create-home erp && mkdir -p /app/media && chown erp /app/media \
    && (ERP_ENV=dev python manage.py collectstatic --noinput >/dev/null 2>&1 || true)
USER erp
EXPOSE 8000
CMD ["gunicorn", "erp.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60", "--access-logfile", "-"]
